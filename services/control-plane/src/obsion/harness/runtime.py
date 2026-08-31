import asyncio
import hashlib
import json
import math
from dataclasses import asdict
from decimal import Decimal
from time import perf_counter
from typing import Any
from uuid import UUID

import structlog
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.application.memory import MemoryService
from obsion.application.run_lifecycle import cancel_active_run_steps
from obsion.artifacts.store import ObjectStore
from obsion.capabilities.gateway import (
    CapabilityGateway,
    GatewayRequest,
    GatewayStatus,
)
from obsion.common.errors import BudgetExceededError, NotFoundError, ObsionError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.data_intelligence.service import DataIntelligenceService
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    Artifact,
    CapabilityDefinition,
    CapabilityVersion,
    Claim,
    ClaimEvidence,
    ClaimVerificationResult,
    DataSource,
    Evidence,
    EvidenceConflict,
    PolicyDecision,
    Run,
    RunConversationSnapshot,
    RunMemorySnapshot,
    RunStep,
    Thread,
    Turn,
    VerificationAssessment,
    VerificationEvidenceLink,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    ActorType,
    AnswerPublicationDecision,
    ArtifactKind,
    Classification,
    EvidenceConflictDisposition,
    EvidenceConflictKind,
    EvidenceConflictSeverity,
    EvidenceRelation,
    EvidenceType,
    RegistryStatus,
    RunStatus,
    StepKind,
    StepStatus,
    VerificationOutcome,
    VerificationRuleOutcome,
    VerificationStatus,
)
from obsion.domain.run_state import is_terminal, validate_run_transition
from obsion.domains.evidence.fabric import EvidenceFabric, EvidenceInput
from obsion.harness.agent_router import AgentRouter, RouteSelection
from obsion.harness.critic import Critic
from obsion.harness.evidence_gaps import gap_step_contract, gap_step_name, select_gap_capabilities
from obsion.harness.incident import IncidentEvidenceFusion, IncidentFusionResult
from obsion.harness.planner import Planner
from obsion.harness.replay import RunReplayService
from obsion.harness.steps import StepExecutor
from obsion.harness.understanding import UnderstandingEngine
from obsion.knowledge.parsers import parse_document
from obsion.model_gateway.compaction import (
    ConversationCompactor,
    conversation_turns_from_snapshots,
)
from obsion.model_gateway.context import (
    ContextBuilder,
    ContextPack,
    ContextSegment,
    TrustLevel,
)
from obsion.model_gateway.evidence_segments import evidence_context_segments
from obsion.model_gateway.gateway import ModelGateway, ModelUnavailableError
from obsion.model_gateway.workspace_context import workspace_context_segments
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.registry.agent_spec import AgentSpec, sandbox_allows_capabilities
from obsion.registry.prompt_pins import (
    SYSTEM_POLICY_PROMPT_NAME,
    load_pinned_templates,
)
from obsion.registry.prompt_render import governed_prompt_values, render_prompt_template
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text
from obsion.telemetry import (
    context_budget_counter,
    conversation_compact_counter,
    critic_coverage,
    replan_counter,
    run_counter,
    run_duration,
    run_steps,
    run_ttft,
    tracer,
)

logger = structlog.get_logger(__name__)


def _observe_run(status: str, started: float) -> None:
    attributes = {"status": status}
    run_counter.add(1, attributes)
    run_duration.record((perf_counter() - started) * 1000, attributes)


class HarnessRuntime:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        gateway: CapabilityGateway,
        model_gateway: ModelGateway,
        object_store: ObjectStore,
    ) -> None:
        self.database = database
        self.settings = settings
        self.gateway = gateway
        self.models = model_gateway
        self.object_store = object_store
        self.events = EventStore()
        self.agent_router = AgentRouter()
        self.understanding = UnderstandingEngine()
        self.planner = Planner()
        self.step_executor = StepExecutor()
        self.critic = Critic()
        self.incident_fusion = IncidentEvidenceFusion()
        self.data = DataIntelligenceService(settings)
        self.memory = MemoryService(settings)
        self.replays = RunReplayService()
        self.audit = AuditWriter()
        self.evidence = EvidenceFabric()

    async def execute(self, organization_id: UUID, run_id: UUID) -> None:
        started = perf_counter()
        with tracer.start_as_current_span("obsion.run") as span:
            span.set_attribute("obsion.run.id", str(run_id))
            try:
                replay = await self._materialize_replay(organization_id, run_id)
                if replay:
                    span.set_attribute("obsion.run.replay", True)
                    _observe_run("REPLAYED", started)
                    return
                await self._prepare(organization_id, run_id)
                should_continue = await self._execute_steps(organization_id, run_id)
                if should_continue:
                    while True:
                        while await self._replan_transient_failures(organization_id, run_id):
                            replan_counter.add(1, {"reason": "transient"})
                            should_continue = await self._execute_steps(organization_id, run_id)
                            if not should_continue:
                                _observe_run("WAITING", started)
                                return
                        if await self._replan_missing_evidence(organization_id, run_id):
                            replan_counter.add(1, {"reason": "missing_evidence"})
                            should_continue = await self._execute_steps(organization_id, run_id)
                            if not should_continue:
                                _observe_run("WAITING", started)
                                return
                            continue
                        replanned = await self._respond(organization_id, run_id)
                        if replanned:
                            replan_counter.add(1, {"reason": "critic_verification_failed"})
                            should_continue = await self._execute_steps(organization_id, run_id)
                            if not should_continue:
                                _observe_run("WAITING", started)
                                return
                            continue
                        break
                    _observe_run("COMPLETED", started)
                else:
                    _observe_run("WAITING", started)
            except Exception as exc:
                logger.exception(
                    "run.execute_failed",
                    run_id=str(run_id),
                    error=f"{type(exc).__name__}: {exc}",
                )
                span.record_exception(exc)
                span.set_attribute("error.type", type(exc).__name__)
                _observe_run("FAILED", started)
                await self._fail(organization_id, run_id, exc)

    async def _materialize_replay(self, organization_id: UUID, run_id: UUID) -> bool:
        async with self.database.sessions() as session, session.begin():
            replay_of_run_id = await session.scalar(
                select(Run.replay_of_run_id).where(
                    Run.id == run_id,
                    Run.organization_id == organization_id,
                )
            )
            if replay_of_run_id is None:
                return False
            await self.replays.materialize(session, organization_id, run_id)
            return True

    async def _load_context(
        self,
        session: AsyncSession,
        organization_id: UUID,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[Run, Turn, Thread, Principal, AgentVersion, AgentDefinition]:
        statement = (
            select(Run, Turn, Thread, AgentVersion, AgentDefinition)
            .join(Turn, Turn.id == Run.turn_id)
            .join(Thread, Thread.id == Turn.thread_id)
            .join(AgentVersion, AgentVersion.id == Run.agent_version_id)
            .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
            .where(Run.id == run_id, Run.organization_id == organization_id)
        )
        if for_update:
            statement = statement.with_for_update(of=Run)
        row = (await session.execute(statement)).one_or_none()
        if row is None:
            raise NotFoundError("Run", run_id)
        run, turn, thread, agent_version, agent_definition = row._tuple()
        principal = await load_principal_by_id(session, organization_id, turn.created_by)
        return run, turn, thread, principal, agent_version, agent_definition

    async def _planner_capabilities(
        self,
        session: AsyncSession,
        organization_id: UUID,
        agent_version: AgentVersion,
        agent_name: str,
    ) -> tuple[frozenset[str], dict[str, Any]]:
        agent_spec = AgentSpec.from_dict(agent_version.spec, source=agent_name)
        sandbox = dict(agent_spec.sandbox)
        if not sandbox_allows_capabilities(sandbox):
            return frozenset(), sandbox
        registered = set(
            await session.scalars(
                select(CapabilityDefinition.name)
                .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
                .where(
                    CapabilityDefinition.organization_id == organization_id,
                    CapabilityDefinition.status == RegistryStatus.ACTIVE,
                    CapabilityVersion.organization_id == organization_id,
                )
            )
        )
        return frozenset(registered.intersection(agent_spec.capabilities)), sandbox

    async def _prepare(self, organization_id: UUID, run_id: UUID) -> None:
        async with self.database.sessions() as session, session.begin():
            (
                run,
                turn,
                thread,
                principal,
                agent_version,
                agent_definition,
            ) = await self._load_context(session, organization_id, run_id, for_update=True)
            if is_terminal(run.status) or run.plan:
                return
            if run.cancellation_requested_at:
                await self._cancel(session, run)
                return
            await self._ingest_attachments(session, run, turn)
            memory_snapshots = await self.memory.capture_run_context(
                session, principal, run, turn, thread
            )
            conversation_snapshots = list(
                await session.scalars(
                    select(RunConversationSnapshot)
                    .where(
                        RunConversationSnapshot.organization_id == organization_id,
                        RunConversationSnapshot.run_id == run.id,
                    )
                    .order_by(RunConversationSnapshot.ordinal)
                )
            )
            data_result = await self.data.understand(session, principal, turn.sanitized_input)
            data_understanding = asdict(data_result)
            understanding = self.understanding.route(turn.sanitized_input, data_understanding)
            route_hint = next(
                (
                    str(item.get("value"))
                    for item in turn.context_refs
                    if isinstance(item, dict) and item.get("type") == "route_hint"
                ),
                None,
            )
            if route_hint == "DATA" and understanding["metrics"]:
                understanding["route"] = "DATA"
                understanding["domain"] = "DATA"
                understanding["intent"] = data_understanding["intent"]
            selection = await self.agent_router.resolve(
                session,
                organization_id,
                str(understanding["route"]),
                question=str(understanding.get("question") or turn.sanitized_input),
                fallback=RouteSelection(
                    agent_version=agent_version,
                    agent_definition=agent_definition,
                ),
            )
            agent_version = selection.agent_version
            agent_definition = selection.agent_definition
            run.agent_version_id = agent_version.id
            skill_snapshot = self.agent_router.skill_snapshot(selection)
            if skill_snapshot is not None:
                understanding["agent"] = agent_definition.name
                understanding["skill"] = skill_snapshot["name"]
            compiled_payload: dict[str, Any] | None = None
            if understanding["route"] in {"DATA", "ANALYTICS"}:
                if not understanding["metrics"]:
                    raise ValidationError(
                        "metric_not_resolved",
                        "No governed metric matches the question",
                    )
                logical_plan = self.data.logical_plan(
                    metric_id=UUID(understanding["metrics"][0]["id"]),
                    dimension_ids=[UUID(item["id"]) for item in understanding["dimensions"]],
                    time_range=understanding["time_range"],
                    filters=[],
                    comparison=understanding.get("comparison"),
                )
                compiled = await self.data.compile(session, principal, logical_plan)
                source = await session.get(DataSource, UUID(compiled.lineage["data_source_id"]))
                if source is None or source.organization_id != organization_id:
                    raise NotFoundError("Data source", compiled.lineage["data_source_id"])
                compiled_payload = jsonable_encoder(asdict(compiled))
                compiled_payload["environment"] = source.environment
            allowed_capabilities, sandbox = await self._planner_capabilities(
                session,
                organization_id,
                agent_version,
                agent_definition.name,
            )
            plan = self.planner.create(
                understanding,
                compiled_data_query=compiled_payload,
                available_capabilities=allowed_capabilities,
            )
            plan_payload = plan.as_dict()
            plan_payload["available_capabilities"] = sorted(allowed_capabilities)
            plan_payload["sandbox"] = sandbox
            if skill_snapshot is not None:
                plan_payload["agent"] = agent_definition.name
                plan_payload["skill"] = skill_snapshot
            run.intent = jsonable_encoder(understanding)
            run.plan = jsonable_encoder(plan_payload)
            run.step_count = 3
            now = utc_now()
            session.add_all(
                [
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=1,
                        name="Observe request context",
                        kind=StepKind.OBSERVE,
                        status=StepStatus.COMPLETED,
                        input_payload={
                            "context_refs": turn.context_refs,
                            "attachment_refs": turn.attachment_refs,
                        },
                        started_at=now,
                        completed_at=now,
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=2,
                        name="Understand request",
                        kind=StepKind.UNDERSTAND,
                        status=StepStatus.COMPLETED,
                        depends_on=[1],
                        input_payload={"question": turn.sanitized_input},
                        started_at=now,
                        completed_at=now,
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=3,
                        name="Create governed execution plan",
                        kind=StepKind.PLAN,
                        status=StepStatus.COMPLETED,
                        depends_on=[2],
                        input_payload={"route": understanding["route"]},
                        started_at=now,
                        completed_at=now,
                    ),
                ]
            )
            capability_ordinals: list[int] = []
            for ordinal, step in enumerate(plan.steps, start=4):
                capability_ordinals.append(ordinal)
                session.add(
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=ordinal,
                        name=step.name,
                        kind=StepKind.CAPABILITY,
                        status=StepStatus.PENDING,
                        depends_on=[value + 3 for value in step.depends_on] or [3],
                        input_payload={
                            "capability": step.capability,
                            "payload": jsonable_encoder(step.payload),
                            "resource": jsonable_encoder(step.resource),
                            "environment": step.environment,
                        },
                        max_retries=1,
                    )
                )
            verify_ordinal = len(plan.steps) + 4
            session.add_all(
                [
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=verify_ordinal,
                        name="Verify evidence and claims",
                        kind=StepKind.VERIFY,
                        status=StepStatus.PENDING,
                        depends_on=capability_ordinals or [3],
                        input_payload={"required_evidence": list(plan.required_evidence)},
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=verify_ordinal + 1,
                        name="Reflect on verification and publication",
                        kind=StepKind.REFLECT,
                        status=StepStatus.PENDING,
                        depends_on=[verify_ordinal],
                        input_payload={},
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=verify_ordinal + 2,
                        name="Publish governed response",
                        kind=StepKind.RESPOND,
                        status=StepStatus.PENDING,
                        depends_on=[verify_ordinal + 1],
                        input_payload={},
                    ),
                ]
            )
            await self.events.append(
                session,
                self._event(
                    run,
                    "context.resolved",
                    {
                        "context_refs": turn.context_refs,
                        "attachment_refs": turn.attachment_refs,
                        "conversation_snapshots": [
                            {
                                "id": str(item.id),
                                "ordinal": item.ordinal,
                                "source_thread_id": str(item.source_thread_id),
                                "source_turn_id": str(item.source_turn_id),
                                "source_run_id": (
                                    str(item.source_run_id) if item.source_run_id else None
                                ),
                                "content_fingerprint": item.content_fingerprint,
                                "classification": item.classification,
                            }
                            for item in conversation_snapshots
                        ],
                        "memory_snapshots": [
                            {
                                "id": str(item.id),
                                "scope": item.scope,
                                "content_fingerprint": item.content_fingerprint,
                                "sensitivity": item.sensitivity,
                            }
                            for item in memory_snapshots
                        ],
                    },
                ),
            )
            await self.events.append(
                session,
                self._event(run, "intent.detected", self._intent_event_payload(run.intent)),
            )
            await self.events.append(
                session,
                self._event(run, "plan.created", self._plan_event_payload(run.plan)),
            )

    async def _ingest_attachments(self, session: AsyncSession, run: Run, turn: Turn) -> None:
        for reference in turn.attachment_refs:
            artifact_id = UUID(str(reference["artifact_id"]))
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.organization_id == run.organization_id,
                )
            )
            if artifact is None:
                raise NotFoundError("Artifact", artifact_id)
            if artifact.inline_content is not None:
                text = json.dumps(artifact.inline_content, ensure_ascii=False, default=str)
            elif artifact.storage_key is not None:
                stored = await self.object_store.get(artifact.storage_key)
                parsed = parse_document(
                    stored.data,
                    artifact.media_type,
                    str(artifact.lineage.get("filename", artifact.title)),
                )
                text = parsed.text
            else:
                raise ValidationError(
                    "attachment_content_missing", "Attached artifact has no readable content"
                )
            normalized = redact_text(text[: self.settings.attachment_context_max_chars])
            evidence = await self.evidence.persist(
                session,
                EvidenceInput(
                    organization_id=run.organization_id,
                    run_id=run.id,
                    evidence_type=EvidenceType.DOCUMENT,
                    source="workspace-artifact",
                    resource=f"artifact:{artifact.id}",
                    observed_at=artifact.updated_at,
                    content={"title": artifact.title, "text": normalized},
                    confidence=1.0,
                    classification=artifact.classification,
                    permissions=("artifact.read",),
                    lineage={
                        "artifact_id": str(artifact.id),
                        "checksum_sha256": artifact.checksum_sha256,
                    },
                ),
            )
            await self.events.append(
                session,
                self._event(
                    run,
                    "evidence.created",
                    {
                        "evidence_id": str(evidence.id),
                        "type": evidence.evidence_type,
                        "source": "workspace-artifact",
                    },
                ),
            )

    async def _execute_steps(self, organization_id: UUID, run_id: UUID) -> bool:
        while True:
            async with self.database.sessions() as session, session.begin():
                run, _, _, principal, _, agent_definition = await self._load_context(
                    session, organization_id, run_id, for_update=True
                )
                if is_terminal(run.status) or run.status == RunStatus.WAITING_APPROVAL:
                    return False
                if run.cancellation_requested_at:
                    await self._cancel(session, run)
                    return False
                if run.deadline_at is not None and ensure_utc(run.deadline_at) <= utc_now():
                    raise BudgetExceededError("deadline", run.deadline_at)
                all_steps = list(
                    await session.scalars(
                        select(RunStep)
                        .where(
                            RunStep.organization_id == organization_id,
                            RunStep.run_id == run_id,
                        )
                        .order_by(RunStep.ordinal)
                    )
                )
                if len(all_steps) > run.max_steps:
                    raise BudgetExceededError("steps", run.max_steps)
                wave = self.step_executor.next_wave(all_steps)
                blocked_ordinals = {step.ordinal for step in wave.blocked}
                ready_steps: tuple[RunStep, ...] = wave.ready
                if blocked_ordinals:
                    for step in all_steps:
                        if step.ordinal in blocked_ordinals:
                            step.status = StepStatus.SKIPPED
                            step.error_code = "dependency_failed"
                            step.completed_at = utc_now()
                    continue
                if not ready_steps:
                    if not wave.deadlocked:
                        return True
                    raise ValidationError(
                        "plan_dependency_deadlock",
                        "The execution plan contains unresolved or cyclic dependencies",
                    )
                if run.step_count + len(ready_steps) > run.max_steps:
                    raise BudgetExceededError("steps", run.max_steps)
                jobs: list[
                    tuple[UUID, dict[str, Any], UUID | None, UUID | None, UUID | None, str]
                ] = []
                for step in ready_steps:
                    step.status = StepStatus.RUNNING
                    step.started_at = step.started_at or utc_now()
                    run.step_count += 1
                    jobs.append(
                        (
                            step.id,
                            dict(step.input_payload),
                            run.agent_version_id,
                            run.model_profile_id,
                            step.capability_version_id,
                            agent_definition.name,
                        )
                    )

            outcomes = await asyncio.gather(
                *[
                    self._invoke_step(
                        organization_id,
                        run_id,
                        step_id,
                        payload,
                        principal,
                        agent_version_id,
                        model_profile_id,
                        capability_version_id,
                        agent_name,
                    )
                    for (
                        step_id,
                        payload,
                        agent_version_id,
                        model_profile_id,
                        capability_version_id,
                        agent_name,
                    ) in jobs
                ]
            )
            if any(outcome == GatewayStatus.WAITING_APPROVAL for outcome in outcomes):
                return False

    async def _replan_transient_failures(
        self,
        organization_id: UUID,
        run_id: UUID,
    ) -> bool:
        """Retry one bounded wave of read-only transient failures through the normal gateway."""
        retryable_codes = {"capability_failed", "capability_timeout", "rate_limit_unavailable"}
        async with self.database.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run)
                .where(Run.id == run_id, Run.organization_id == organization_id)
                .with_for_update()
            )
            if run is None:
                raise NotFoundError("Run", run_id)
            if is_terminal(run.status) or run.status == RunStatus.WAITING_APPROVAL:
                return False
            if run.cancellation_requested_at:
                await self._cancel(session, run)
                return False
            steps = list(
                await session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.organization_id == organization_id,
                        RunStep.run_id == run_id,
                        RunStep.kind == StepKind.CAPABILITY,
                    )
                    .order_by(RunStep.ordinal)
                )
            )
            retry_ordinals = {
                step.ordinal
                for step in steps
                if step.status == StepStatus.FAILED
                and step.error_code in retryable_codes
                and step.retry_count < step.max_retries
            }
            if not retry_ordinals:
                return False
            changed = True
            while changed:
                changed = False
                for step in steps:
                    if (
                        step.status == StepStatus.SKIPPED
                        and step.ordinal not in retry_ordinals
                        and any(value in retry_ordinals for value in step.depends_on)
                    ):
                        retry_ordinals.add(step.ordinal)
                        changed = True

            validate_run_transition(run.status, RunStatus.REPLANNING)
            previous_status = run.status
            run.status = RunStatus.REPLANNING
            await self.events.append(
                session,
                self._event(
                    run,
                    "run.state_changed",
                    {
                        "from": previous_status,
                        "to": RunStatus.REPLANNING,
                        "reason": "transient_read_only_failure",
                    },
                ),
            )
            for step in steps:
                if step.ordinal not in retry_ordinals:
                    continue
                if step.status == StepStatus.FAILED:
                    step.retry_count += 1
                step.status = StepStatus.PENDING
                step.started_at = None
                step.completed_at = None
                step.error_code = None
                step.output_ref = None
            plan = dict(run.plan)
            history = list(plan.get("replans", []))
            history.append(
                {
                    "attempt": len(history) + 1,
                    "reason": "transient_read_only_failure",
                    "step_ordinals": sorted(retry_ordinals),
                }
            )
            plan["replans"] = history
            run.plan = plan
            await self.events.append(
                session,
                self._event(
                    run,
                    "plan.updated",
                    {"replan": history[-1]},
                ),
            )
            validate_run_transition(run.status, RunStatus.RUNNING)
            run.status = RunStatus.RUNNING
            await self.events.append(
                session,
                self._event(
                    run,
                    "run.state_changed",
                    {
                        "from": RunStatus.REPLANNING,
                        "to": RunStatus.RUNNING,
                        "reason": "recovery_plan_ready",
                    },
                ),
            )
            return True

    async def _apply_gap_replan(
        self,
        session: AsyncSession,
        run: Run,
        turn: Turn,
        steps: list[RunStep],
        *,
        missing: tuple[str, ...],
        reason: str,
    ) -> bool:
        """Insert unused authorized capabilities ahead of VERIFY/REFLECT/RESPOND."""
        if not missing:
            return False
        plan = dict(run.plan)
        history = list(plan.get("replans", []))
        critic_attempts = sum(
            1
            for item in history
            if item.get("reason") in {"critic_missing_evidence", "critic_verification_failed"}
        )
        if critic_attempts >= self.settings.run_max_critic_replans:
            return False
        capability_steps = [step for step in steps if step.kind == StepKind.CAPABILITY]
        attempted = {
            str(step.input_payload.get("capability"))
            for step in capability_steps
            if isinstance(step.input_payload, dict) and step.input_payload.get("capability")
        }
        declared = plan.get("available_capabilities")
        if isinstance(declared, list):
            available = frozenset(str(item) for item in declared)
        elif run.agent_version_id is not None:
            agent_row = (
                await session.execute(
                    select(AgentVersion, AgentDefinition)
                    .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
                    .where(
                        AgentVersion.id == run.agent_version_id,
                        AgentVersion.organization_id == run.organization_id,
                    )
                )
            ).one_or_none()
            if agent_row is None:
                available = frozenset()
            else:
                agent_version, agent_definition = agent_row._tuple()
                available, _sandbox = await self._planner_capabilities(
                    session, run.organization_id, agent_version, agent_definition.name
                )
        else:
            available = frozenset()
        selected = select_gap_capabilities(
            missing, available=available, attempted=frozenset(attempted)
        )
        if not selected:
            return False
        verify_step = next((step for step in reversed(steps) if step.kind == StepKind.VERIFY), None)
        reflect_step = next(
            (step for step in reversed(steps) if step.kind == StepKind.REFLECT), None
        )
        respond_step = next(
            (step for step in reversed(steps) if step.kind == StepKind.RESPOND), None
        )
        if verify_step is None or respond_step is None:
            return False
        added = len(selected)
        if len(steps) + added > run.max_steps:
            return False
        question = turn.sanitized_input
        template = next(
            (
                dict(step.input_payload)
                for step in reversed(capability_steps)
                if isinstance(step.input_payload, dict)
            ),
            {},
        )
        original_verify = verify_step.ordinal
        original_reflect = reflect_step.ordinal if reflect_step is not None else None
        original_respond = respond_step.ordinal
        verify_step.ordinal = original_verify + 1000
        if reflect_step is not None and original_reflect is not None:
            reflect_step.ordinal = original_reflect + 1000
        respond_step.ordinal = original_respond + 1000
        await session.flush()
        verify_step.ordinal = original_verify + added
        if reflect_step is not None and original_reflect is not None:
            reflect_step.ordinal = original_reflect + added
        respond_step.ordinal = original_respond + added
        await session.flush()
        new_ordinals: list[int] = []
        completed_caps = [
            step.ordinal for step in capability_steps if step.status == StepStatus.COMPLETED
        ]
        last_capability = max(completed_caps, default=3)
        plan_steps = list(plan.get("steps", []))
        for offset, (missing_type, capability) in enumerate(selected):
            ordinal = original_verify + offset
            new_ordinals.append(ordinal)
            contract = gap_step_contract(
                capability,
                missing_type,
                question=question,
                template=template,
            )
            session.add(
                RunStep(
                    organization_id=run.organization_id,
                    run_id=run.id,
                    ordinal=ordinal,
                    name=gap_step_name(capability, missing_type),
                    kind=StepKind.CAPABILITY,
                    status=StepStatus.PENDING,
                    depends_on=[last_capability],
                    input_payload=jsonable_encoder(contract),
                    max_retries=1,
                )
            )
            plan_steps.append(
                {
                    "ordinal": len(plan_steps) + 1,
                    "name": gap_step_name(capability, missing_type),
                    "capability": capability,
                    "payload": contract["payload"],
                    "resource": contract["resource"],
                    "environment": contract["environment"],
                    "depends_on": (len(plan_steps),) if plan_steps else (),
                    "replan_reason": reason,
                }
            )
        verify_depends = [int(item) for item in verify_step.depends_on] + new_ordinals
        verify_step.depends_on = list(dict.fromkeys(verify_depends))
        if reflect_step is not None:
            reflect_step.depends_on = [verify_step.ordinal]
            respond_step.depends_on = [reflect_step.ordinal]
        else:
            respond_step.depends_on = [verify_step.ordinal]
        validate_run_transition(run.status, RunStatus.REPLANNING)
        previous_status = run.status
        run.status = RunStatus.REPLANNING
        await self.events.append(
            session,
            self._event(
                run,
                "run.state_changed",
                {
                    "from": previous_status,
                    "to": RunStatus.REPLANNING,
                    "reason": reason,
                },
            ),
        )
        history.append(
            {
                "attempt": len(history) + 1,
                "reason": reason,
                "missing_evidence": list(missing),
                "capabilities": [capability for _, capability in selected],
                "step_ordinals": new_ordinals,
            }
        )
        plan["replans"] = history
        plan["steps"] = plan_steps
        run.plan = plan
        await self.events.append(
            session,
            self._event(run, "plan.updated", {"replan": history[-1]}),
        )
        validate_run_transition(run.status, RunStatus.RUNNING)
        run.status = RunStatus.RUNNING
        await self.events.append(
            session,
            self._event(
                run,
                "run.state_changed",
                {
                    "from": RunStatus.REPLANNING,
                    "to": RunStatus.RUNNING,
                    "reason": "recovery_plan_ready",
                },
            ),
        )
        return True

    async def _replan_missing_evidence(
        self,
        organization_id: UUID,
        run_id: UUID,
    ) -> bool:
        """Append unused authorized capabilities for Critic-reported evidence gaps."""
        async with self.database.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run)
                .where(Run.id == run_id, Run.organization_id == organization_id)
                .with_for_update()
            )
            if run is None:
                raise NotFoundError("Run", run_id)
            if is_terminal(run.status) or run.status == RunStatus.WAITING_APPROVAL:
                return False
            if run.cancellation_requested_at:
                await self._cancel(session, run)
                return False
            turn = await session.scalar(select(Turn).where(Turn.id == run.turn_id))
            if turn is None:
                raise NotFoundError("Turn", run.turn_id)
            required_types = tuple(str(item) for item in run.plan.get("required_evidence", []))
            if not required_types:
                return False
            evidence = list(
                await session.scalars(
                    select(Evidence).where(
                        Evidence.organization_id == organization_id,
                        Evidence.run_id == run_id,
                    )
                )
            )
            missing = Critic.missing_required_types(
                Critic.substantive_records(evidence), required_types
            )
            if not missing:
                return False
            steps = list(
                await session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.organization_id == organization_id,
                        RunStep.run_id == run_id,
                    )
                    .order_by(RunStep.ordinal)
                    .with_for_update()
                )
            )
            return await self._apply_gap_replan(
                session,
                run,
                turn,
                steps,
                missing=missing,
                reason="critic_missing_evidence",
            )

    async def _invoke_step(
        self,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID,
        payload: dict[str, Any],
        principal: Principal,
        agent_version_id: UUID | None,
        model_profile_id: UUID | None,
        capability_version_id: UUID | None,
        agent_name: str,
    ) -> GatewayStatus:
        try:
            async with self.database.sessions() as invoke_session, invoke_session.begin():
                result = await self.gateway.invoke(
                    invoke_session,
                    GatewayRequest(
                        principal=principal,
                        capability_name=payload["capability"],
                        payload=payload["payload"],
                        resource=payload["resource"],
                        environment=payload["environment"],
                        agent_name=agent_name,
                        agent_version_id=agent_version_id,
                        model_profile_id=model_profile_id,
                        capability_version_id=capability_version_id,
                        run_id=run_id,
                        step_id=step_id,
                    ),
                )
        except ObsionError as exc:
            await self._finish_step(
                organization_id,
                run_id,
                step_id,
                StepStatus.FAILED,
                error_code=exc.code,
            )
            return GatewayStatus.FAILED
        if result.status == GatewayStatus.COMPLETED:
            await self._finish_step(
                organization_id,
                run_id,
                step_id,
                StepStatus.COMPLETED,
                output_ref=str(result.evidence_id),
                capability_version_id=result.capability_version_id,
            )
        elif result.status == GatewayStatus.WAITING_APPROVAL:
            async with self.database.sessions() as wait_session, wait_session.begin():
                wait_run = await wait_session.get(Run, run_id, with_for_update=True)
                wait_step = await wait_session.get(RunStep, step_id, with_for_update=True)
                if wait_run and wait_step:
                    if wait_run.status != RunStatus.WAITING_APPROVAL:
                        validate_run_transition(wait_run.status, RunStatus.WAITING_APPROVAL)
                        wait_run.status = RunStatus.WAITING_APPROVAL
                    wait_run.lease_owner = None
                    wait_run.lease_expires_at = None
                    wait_step.status = StepStatus.WAITING_APPROVAL
                    wait_step.capability_version_id = result.capability_version_id
        else:
            await self._finish_step(
                organization_id,
                run_id,
                step_id,
                StepStatus.FAILED,
                error_code=result.error_code,
                capability_version_id=result.capability_version_id,
            )
        return result.status

    async def _finish_step(
        self,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID,
        status: StepStatus,
        *,
        output_ref: str | None = None,
        error_code: str | None = None,
        capability_version_id: UUID | None = None,
    ) -> None:
        async with self.database.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run)
                .where(Run.id == run_id, Run.organization_id == organization_id)
                .with_for_update()
            )
            if run is None:
                raise NotFoundError("Run", run_id)
            if is_terminal(run.status) or run.cancellation_requested_at:
                if run.status == RunStatus.CANCELLED or run.cancellation_requested_at:
                    await self._cancel(session, run)
                return
            step = await session.scalar(
                select(RunStep)
                .where(
                    RunStep.id == step_id,
                    RunStep.organization_id == organization_id,
                    RunStep.run_id == run_id,
                )
                .with_for_update()
            )
            if step is None:
                raise NotFoundError("Run step", step_id)
            step.status = status
            step.output_ref = output_ref
            step.error_code = error_code
            step.capability_version_id = capability_version_id or step.capability_version_id
            step.completed_at = utc_now()

    async def _respond(self, organization_id: UUID, run_id: UUID) -> bool:
        async with self.database.sessions() as session, session.begin():
            run, turn, thread, _, agent_version, agent_definition = await self._load_context(
                session, organization_id, run_id, for_update=True
            )
            if is_terminal(run.status):
                return False
            if run.cancellation_requested_at:
                await self._cancel(session, run)
                return False
            steps = list(
                await session.scalars(
                    select(RunStep)
                    .where(
                        RunStep.organization_id == organization_id,
                        RunStep.run_id == run_id,
                    )
                    .order_by(RunStep.ordinal)
                    .with_for_update()
                )
            )
            verify_step = next(
                (step for step in reversed(steps) if step.kind == StepKind.VERIFY), None
            )
            reflect_step = next(
                (step for step in reversed(steps) if step.kind == StepKind.REFLECT), None
            )
            respond_step = next(
                (step for step in reversed(steps) if step.kind == StepKind.RESPOND), None
            )
            evidence = list(
                await session.scalars(
                    select(Evidence)
                    .where(
                        Evidence.organization_id == organization_id,
                        Evidence.run_id == run_id,
                    )
                    .order_by(Evidence.ingested_at)
                )
            )
            memory_snapshots = list(
                await session.scalars(
                    select(RunMemorySnapshot)
                    .where(
                        RunMemorySnapshot.organization_id == organization_id,
                        RunMemorySnapshot.run_id == run_id,
                    )
                    .order_by(RunMemorySnapshot.ordinal)
                )
            )
            conversation_snapshots = list(
                await session.scalars(
                    select(RunConversationSnapshot)
                    .where(
                        RunConversationSnapshot.organization_id == organization_id,
                        RunConversationSnapshot.run_id == run_id,
                    )
                    .order_by(RunConversationSnapshot.ordinal)
                )
            )
            evidence_free_response = self._evidence_free_response_allowed(run)
            if not evidence and not evidence_free_response:
                failed_steps = list(
                    await session.scalars(
                        select(RunStep).where(
                            RunStep.run_id == run_id,
                            RunStep.status == StepStatus.FAILED,
                        )
                    )
                )
                codes = sorted({step.error_code or "unknown" for step in failed_steps})
                raise ObsionError(
                    "capabilities_unavailable",
                    "No authorized evidence could be collected",
                    status_code=503,
                    details={"step_errors": codes},
                )
            answer, claims = await self._synthesize(
                session,
                run,
                turn,
                agent_version,
                agent_definition,
                evidence,
                memory_snapshots,
                conversation_snapshots,
            )
            incident_fusion: IncidentFusionResult | None = None
            if run.plan.get("route") == "INCIDENT":
                incident_fusion = self.incident_fusion.fuse(evidence)
            citations: list[dict[str, Any]] = []
            if run.plan.get("route") in {"KNOWLEDGE", "SUPPORT"}:
                citations = self._knowledge_citations(claims, evidence)
                if citations:
                    answer = self._append_knowledge_citations(answer, citations)
                else:
                    # A knowledge answer without a substantive, citeable source must
                    # be an explicit unknown rather than an unverified model response.
                    answer = self._knowledge_unknown_answer()
                    claims = []
            elif run.plan.get("route") == "ENGINEERING":
                citations = self._code_citations(claims, evidence)
                if citations:
                    answer = self._append_code_citations(answer, citations)
                else:
                    answer = self._code_unknown_answer()
                    claims = []
            await session.refresh(
                run,
                attribute_names=["status", "cancellation_requested_at"],
                with_for_update=True,
            )
            if is_terminal(run.status):
                return False
            if run.cancellation_requested_at:
                await self._cancel(session, run)
                return False
            required_types = tuple(run.plan.get("required_evidence", []))
            self._start_core_step(run, verify_step)
            critic = self.critic.verify(
                evidence,
                required_types=required_types,
                claims=claims,
                claims_required=not evidence_free_response,
                route=run.plan.get("route"),
                question=turn.sanitized_input,
                answer=answer,
                time_range=(
                    run.intent.get("time_range")
                    if isinstance(run.intent.get("time_range"), dict)
                    else None
                ),
                additional_conflicts=(
                    incident_fusion.conflicts if incident_fusion is not None else ()
                ),
            )
            self._complete_core_step(verify_step, output_ref="critic.completed")
            decision = self._reflect_decision(
                critic=critic, evidence_free_response=evidence_free_response
            )
            if decision == "REPLAN":
                applied = await self._apply_gap_replan(
                    session,
                    run,
                    turn,
                    steps,
                    missing=tuple(str(item) for item in critic.missing_evidence),
                    reason="critic_verification_failed",
                )
                if applied:
                    self._reopen_core_step(run, verify_step)
                    return True
                decision = "WITHHOLD"
            self._complete_reflect_step(
                run,
                reflect_step,
                critic=critic,
                evidence_free_response=evidence_free_response,
                decision=decision,
            )
            verification_status = (
                VerificationStatus.VERIFIED if critic.verified else VerificationStatus.PARTIAL
            )
            self._start_core_step(run, respond_step)
            claim_models: list[Claim] = []
            evidence_by_id = {str(item.id): item for item in evidence}
            for ordinal, item in enumerate(claims, start=1):
                candidate = next(
                    (
                        candidate
                        for candidate in (incident_fusion.candidates if incident_fusion else ())
                        if set(candidate.evidence_ids) == set(item["evidence_ids"])
                    ),
                    None,
                )
                claim = Claim(
                    organization_id=organization_id,
                    run_id=run.id,
                    ordinal=ordinal,
                    statement=item["statement"],
                    confidence=min(
                        float(item.get("confidence", critic.confidence)), critic.confidence
                    ),
                    verification_status=verification_status,
                    critic_notes={
                        "checks": critic.checks,
                        **(
                            {
                                "incident_candidate_rank": candidate.rank,
                                "incident_candidate_score": candidate.score,
                                "incident_evidence_types": list(candidate.evidence_types),
                            }
                            if candidate is not None
                            else {}
                        ),
                    },
                    created_at=utc_now(),
                )
                session.add(claim)
                await session.flush()
                claim_models.append(claim)
                for evidence_id in item["evidence_ids"]:
                    if evidence_id in evidence_by_id:
                        session.add(
                            ClaimEvidence(
                                organization_id=organization_id,
                                run_id=run.id,
                                claim_id=claim.id,
                                evidence_id=evidence_by_id[evidence_id].id,
                            )
                        )
            result_artifacts = self._data_result_artifacts(
                run, turn, thread, evidence
            ) + self._engineering_result_artifacts(run, turn, thread, evidence)
            critic_coverage.record(
                float(critic.coverage),
                {
                    "route": str(run.plan.get("route") or "UNKNOWN"),
                    "verified": "true" if critic.verified else "false",
                },
            )
            verification_assessment_id = await self._persist_verification_assessment(
                session,
                run=run,
                verify_step=verify_step,
                critic=critic,
                claims=claims,
                claim_models=claim_models,
                evidence=evidence,
                classification=self._highest_classification(
                    evidence,
                    memory_snapshots,
                    conversation_snapshots,
                ),
            )
            session.add_all(result_artifacts)
            await session.flush()
            dashboard = self._workspace_dashboard_artifact(run, turn, thread, result_artifacts)
            if dashboard is not None:
                session.add(dashboard)
                await session.flush()
                result_artifacts = [*result_artifacts, dashboard]
            answer_content: dict[str, Any] = {
                "markdown": answer,
                "verification": jsonable_encoder(asdict(critic)),
                "claim_ids": [str(item.id) for item in claim_models],
                "citations": citations,
            }
            if verification_assessment_id is not None:
                answer_content["verification_assessment_id"] = str(verification_assessment_id)
            answer_lineage: dict[str, Any] = {
                "run_id": str(run.id),
                "result_artifact_ids": [str(item.id) for item in result_artifacts],
            }
            if incident_fusion is not None:
                answer_content["incident_fusion"] = jsonable_encoder(incident_fusion.as_dict())
                answer_lineage["incident_fusion"] = {
                    "candidate_count": len(incident_fusion.candidates)
                }
            artifact = Artifact(
                organization_id=organization_id,
                workspace_id=thread.workspace_id,
                run_id=run.id,
                kind=ArtifactKind.TEXT,
                title="Obsion answer",
                media_type="text/markdown",
                inline_content=answer_content,
                classification=self._highest_classification(
                    evidence,
                    memory_snapshots,
                    conversation_snapshots,
                ),
                acl={"users": [str(turn.created_by)]},
                lineage=answer_lineage,
            )
            session.add(artifact)
            await session.flush()
            report = self._workspace_report_artifact(
                run,
                turn,
                thread,
                answer=artifact,
                result_artifacts=result_artifacts,
                citations=citations,
                incident_fusion=incident_fusion,
            )
            if report is not None:
                session.add(report)
                await session.flush()
                result_artifacts = [*result_artifacts, report]
                artifact.lineage = {
                    **artifact.lineage,
                    "report_artifact_id": str(report.id),
                }
            await self.events.append(session, self._event(run, "critic.completed", asdict(critic)))
            await self.events.append(
                session,
                self._event(run, "answer.delta", {"delta": answer, "final": True}),
            )
            run_ttft.record(
                max(0.0, (utc_now() - ensure_utc(run.created_at)).total_seconds() * 1000),
                {"final": "true"},
            )
            run_steps.record(run.step_count, {"route": str(run.plan.get("route") or "UNKNOWN")})
            for result_artifact in result_artifacts:
                await self.events.append(
                    session,
                    self._event(
                        run,
                        "artifact.created",
                        {
                            "artifact_id": str(result_artifact.id),
                            "kind": result_artifact.kind,
                        },
                    ),
                )
            await self.events.append(
                session,
                self._event(
                    run,
                    "artifact.created",
                    {"artifact_id": str(artifact.id), "kind": artifact.kind},
                ),
            )
            validate_run_transition(run.status, RunStatus.COMPLETED)
            run.status = RunStatus.COMPLETED
            completed_at = utc_now()
            run.completed_at = completed_at
            run.lease_owner = None
            run.lease_expires_at = None
            self._complete_core_step(respond_step, output_ref=str(artifact.id))
            await self.events.append(
                session,
                self._event(
                    run,
                    "run.completed",
                    {
                        "artifact_id": str(artifact.id),
                        "artifact_ids": [
                            *(str(item.id) for item in result_artifacts),
                            str(artifact.id),
                        ],
                        "verified": critic.verified,
                        "confidence": critic.confidence,
                    },
                ),
            )
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=run.organization_id,
                    correlation_id=run.id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    action="run.complete",
                    resource_type="run",
                    resource_id=str(run.id),
                    outcome="SUCCESS",
                    metadata={
                        "turn_id": str(turn.id),
                        "thread_id": str(thread.id),
                        "artifact_id": str(artifact.id),
                        "artifact_ids": [str(item.id) for item in result_artifacts]
                        + [str(artifact.id)],
                        "verified": critic.verified,
                        "confidence": critic.confidence,
                    },
                    latency_ms=(
                        max(
                            0,
                            int((completed_at - ensure_utc(run.started_at)).total_seconds() * 1000),
                        )
                        if run.started_at is not None
                        else None
                    ),
                    agent_version_id=run.agent_version_id,
                    model_profile_id=run.model_profile_id,
                    resource={"run_id": str(run.id), "thread_id": str(thread.id)},
                    result_classification=self._highest_classification(
                        evidence,
                        memory_snapshots,
                        conversation_snapshots,
                    ),
                ),
            )
            return False

    async def _persist_verification_assessment(
        self,
        session: AsyncSession,
        *,
        run: Run,
        verify_step: RunStep | None,
        critic: Any,
        claims: list[dict[str, Any]],
        claim_models: list[Claim],
        evidence: list[Evidence],
        classification: Classification,
    ) -> UUID | None:
        """Persist the immutable verification graph for replay and audit.

        A VERIFIED assessment is publishable only when a gateway PolicyDecision
        exists for the same run.  A conversation or a run without that decision
        is recorded as WITHHOLD/PARTIAL instead of bypassing the database
        admission constraints.  The method intentionally has no model or
        executor dependencies: the persisted graph is a projection of Critic's
        deterministic result.
        """
        if verify_step is None:
            return None

        now = utc_now()
        evidence_by_id = {str(item.id): item for item in evidence}
        policy_decision = await session.scalar(
            select(PolicyDecision)
            .where(
                PolicyDecision.organization_id == run.organization_id,
                PolicyDecision.run_id == run.id,
            )
            .order_by(PolicyDecision.created_at.desc())
            .limit(1)
        )
        claim_generation = max((int(item.generation) for item in claim_models), default=1)
        assessment_verified = bool(critic.verified and claim_models and policy_decision)
        policy_id = policy_decision.id if assessment_verified and policy_decision else None
        outcome = (
            VerificationOutcome.VERIFIED if assessment_verified else VerificationOutcome.PARTIAL
        )
        publication = (
            AnswerPublicationDecision.PUBLISH
            if assessment_verified
            else AnswerPublicationDecision.WITHHOLD
        )
        rules = [
            "question_coverage",
            "required_evidence",
            "claim_links",
            "temporal_consistency",
            "metric_definition_consistency",
            "alternative_explanations",
            "sql_reliability",
            "hallucination_guard",
        ]
        ruleset_snapshot = {
            "version": "phase20.critic.v2",
            "route": str(run.plan.get("route", "UNKNOWN")),
            "required_evidence": list(run.plan.get("required_evidence", [])),
            "rules": rules,
        }
        ruleset_fingerprint = hashlib.sha256(
            json.dumps(ruleset_snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        input_snapshot = {
            "evidence": [
                {
                    "id": str(item.id),
                    "fingerprint": item.content_fingerprint,
                    "observed_at": ensure_utc(item.observed_at).isoformat(),
                }
                for item in evidence
            ],
            "claims": [
                {
                    "statement": str(claim.get("statement", "")),
                    "evidence_ids": [str(value) for value in claim.get("evidence_ids", [])],
                }
                for claim in claims
            ],
        }
        input_fingerprint = hashlib.sha256(
            json.dumps(input_snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        policy_snapshot = (
            jsonable_encoder(
                {
                    "id": policy_decision.id,
                    "action": policy_decision.action,
                    "effect": policy_decision.effect,
                    "risk_level": policy_decision.risk_level,
                    "reason_codes": policy_decision.reason_codes,
                }
            )
            if policy_decision is not None
            else {}
        )
        assessment = VerificationAssessment(
            organization_id=run.organization_id,
            run_id=run.id,
            verify_step_id=verify_step.id,
            attempt=1,
            claim_generation=claim_generation,
            outcome=outcome,
            publication_decision=publication,
            evaluator="independent-evidence-critic",
            evaluator_version="2.0.0",
            route=str(run.plan.get("route", "UNKNOWN")),
            rules=rules,
            ruleset_snapshot=ruleset_snapshot,
            ruleset_fingerprint=ruleset_fingerprint,
            input_fingerprint=input_fingerprint,
            policy_snapshot=policy_snapshot,
            policy_decision_id=policy_id,
            minimum_coverage=Decimal("1.0000"),
            minimum_confidence=Decimal("0.8000"),
            coverage=Decimal(str(round(float(critic.coverage), 4))),
            confidence=Decimal(str(round(float(critic.confidence), 4))),
            checks=dict(critic.checks),
            missing_requirements=list(critic.missing_evidence),
            high_conflict_count=0,
            classification=classification,
            error_code=None,
            duration_ms=0,
            replay_lineage={"source": "harness_runtime", "deterministic": True},
            completed_at=now,
            created_at=now,
        )
        session.add(assessment)
        await session.flush()

        for ordinal, (claim, claim_model) in enumerate(
            zip(claims, claim_models, strict=True), start=1
        ):
            linked_ids = [
                str(value)
                for value in claim.get("evidence_ids", [])
                if str(value) in evidence_by_id
            ]
            claim_ok = bool(linked_ids) and len(linked_ids) == len(claim.get("evidence_ids", []))
            claim_outcome = (
                VerificationOutcome.VERIFIED
                if assessment_verified and claim_ok
                else VerificationOutcome.PARTIAL
            )
            reason_codes: list[str] = []
            if not claim_ok:
                reason_codes.append("claim_evidence_link_invalid")
            if critic.missing_evidence:
                reason_codes.append("required_evidence_missing")
            if critic.conflicts:
                reason_codes.append("evidence_conflict")
            result = ClaimVerificationResult(
                organization_id=run.organization_id,
                run_id=run.id,
                assessment_id=assessment.id,
                claim_id=claim_model.id,
                claim_generation=claim_generation,
                ordinal=ordinal,
                outcome=claim_outcome,
                coverage=Decimal(str(round(float(critic.coverage), 4))),
                confidence=Decimal(
                    str(round(float(min(claim_model.confidence, critic.confidence)), 4))
                ),
                checks=dict(critic.checks),
                reason_codes=reason_codes,
                material=True,
                classification=classification,
                created_at=now,
            )
            session.add(result)
            await session.flush()
            for evidence_id in linked_ids:
                item = evidence_by_id[evidence_id]
                session.add(
                    VerificationEvidenceLink(
                        organization_id=run.organization_id,
                        run_id=run.id,
                        assessment_id=assessment.id,
                        claim_result_id=result.id,
                        evidence_id=item.id,
                        observation_id=None,
                        rule="claim_evidence_link",
                        rule_outcome=(
                            VerificationRuleOutcome.PASSED
                            if claim_ok
                            else VerificationRuleOutcome.FAILED
                        ),
                        relation=EvidenceRelation.SUPPORTS,
                        reason_codes=reason_codes,
                        source_fingerprint=item.content_fingerprint,
                        classification=item.classification,
                        created_at=now,
                    )
                )

        persisted_conflicts = 0
        for conflict in critic.conflicts:
            left_id = str(conflict.get("left_evidence_id", ""))
            right_id = str(conflict.get("right_evidence_id", ""))
            left = evidence_by_id.get(left_id)
            right = evidence_by_id.get(right_id)
            if left is None or right is None or left.id == right.id:
                # Provider-declared conflicts may not identify a pair.  They are
                # still present in the Critic payload, but cannot satisfy the
                # relational conflict table's two-evidence invariant.
                continue
            try:
                kind = EvidenceConflictKind(str(conflict.get("kind", "VALUE")).upper())
            except ValueError:
                kind = EvidenceConflictKind.VALUE
            try:
                severity = EvidenceConflictSeverity(str(conflict.get("severity", "MEDIUM")).upper())
            except ValueError:
                severity = EvidenceConflictSeverity.MEDIUM
            subject = str(conflict.get("subject") or left.resource or "evidence")[:500]
            measure = str(conflict.get("measure") or kind.value).strip()[:300]
            unit = str(conflict.get("unit") or "unknown").strip()[:120]
            environment = str(
                conflict.get("environment")
                or left.lineage.get("environment")
                or right.lineage.get("environment")
                or "unknown"
            ).strip()[:120]
            definition_version = str(
                conflict.get("definition_version")
                or left.lineage.get("definition_version")
                or right.lineage.get("definition_version")
                or "unknown"
            ).strip()[:200]
            scope_fingerprint = hashlib.sha256(
                json.dumps(
                    {"left": left.resource, "right": right.resource, "environment": environment},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            conflict_fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "left": left_id,
                        "right": right_id,
                        "kind": kind.value,
                        "reason": str(conflict.get("reason", "")),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            session.add(
                EvidenceConflict(
                    organization_id=run.organization_id,
                    run_id=run.id,
                    assessment_id=assessment.id,
                    left_evidence_id=left.id,
                    right_evidence_id=right.id,
                    left_observation_id=None,
                    right_observation_id=None,
                    kind=kind,
                    severity=severity,
                    disposition=EvidenceConflictDisposition.UNRESOLVED,
                    subject=subject or "evidence",
                    measure=measure or kind.value,
                    unit=unit or "unknown",
                    environment=environment or "unknown",
                    definition_version=definition_version or "unknown",
                    scope_fingerprint=scope_fingerprint,
                    valid_from=min(ensure_utc(left.observed_at), ensure_utc(right.observed_at)),
                    valid_to=None,
                    details=jsonable_encoder(conflict),
                    conflict_fingerprint=conflict_fingerprint,
                    classification=self._highest_classification([left, right]),
                    created_at=now,
                )
            )
            persisted_conflicts += int(
                severity in {EvidenceConflictSeverity.HIGH, EvidenceConflictSeverity.CRITICAL}
            )
        assessment.high_conflict_count = persisted_conflicts
        return assessment.id

    @staticmethod
    def _evidence_free_response_allowed(run: Run) -> bool:
        plan_steps = run.plan.get("steps", [])
        return (
            run.plan.get("route") == "CONVERSATION"
            and not run.plan.get("required_evidence")
            and isinstance(plan_steps, list)
            and not plan_steps
        )

    @staticmethod
    def _reflect_decision(*, critic: Any, evidence_free_response: bool) -> str:
        if critic.verified or evidence_free_response:
            return "RESPOND"
        if critic.missing_evidence:
            return "REPLAN"
        return "WITHHOLD"

    def _complete_reflect_step(
        self,
        run: Run,
        reflect_step: RunStep | None,
        *,
        critic: Any,
        evidence_free_response: bool,
        decision: str | None = None,
    ) -> str:
        chosen = decision or self._reflect_decision(
            critic=critic, evidence_free_response=evidence_free_response
        )
        self._start_core_step(run, reflect_step)
        if reflect_step is not None and reflect_step.status == StepStatus.RUNNING:
            payload = dict(reflect_step.input_payload or {})
            payload.update(
                jsonable_encoder(
                    {
                        "decision": chosen,
                        "verified": bool(critic.verified),
                        "missing_evidence": list(critic.missing_evidence),
                        "conflict_count": len(critic.conflicts),
                        "coverage": critic.coverage,
                        "evidence_free_response": evidence_free_response,
                    }
                )
            )
            reflect_step.input_payload = payload
        self._complete_core_step(reflect_step, output_ref=f"reflect.{chosen.lower()}")
        return chosen

    @staticmethod
    def _reopen_core_step(run: Run, step: RunStep | None) -> None:
        if step is None or step.status == StepStatus.PENDING:
            return
        if step.status == StepStatus.RUNNING and run.step_count > 0:
            run.step_count -= 1
        step.status = StepStatus.PENDING
        step.started_at = None
        step.completed_at = None
        step.output_ref = None
        step.error_code = None

    @staticmethod
    def _start_core_step(run: Run, step: RunStep | None) -> None:
        if step is None or step.status == StepStatus.COMPLETED:
            return
        if step.status not in {StepStatus.PENDING, StepStatus.WAITING_APPROVAL, StepStatus.RUNNING}:
            return
        if step.status != StepStatus.RUNNING:
            run.step_count += 1
        step.status = StepStatus.RUNNING
        step.started_at = step.started_at or utc_now()

    @staticmethod
    def _complete_core_step(step: RunStep | None, *, output_ref: str | None = None) -> None:
        if step is None or step.status == StepStatus.COMPLETED:
            return
        if step.status != StepStatus.RUNNING:
            return
        step.status = StepStatus.COMPLETED
        step.output_ref = output_ref
        step.completed_at = utc_now()

    def _data_result_artifacts(
        self,
        run: Run,
        turn: Turn,
        thread: Thread,
        evidence: list[Evidence],
    ) -> list[Artifact]:
        if run.plan.get("route") not in {"DATA", "ANALYTICS"}:
            return []
        data_evidence = next(
            (item for item in evidence if item.evidence_type == EvidenceType.DATA), None
        )
        if data_evidence is None:
            return []
        steps = run.plan.get("steps", [])
        query_step = next(
            (
                item
                for item in steps
                if isinstance(item, dict) and item.get("capability") == "data.query"
            ),
            {},
        )
        payload = query_step.get("payload", {}) if isinstance(query_step, dict) else {}
        resource = query_step.get("resource", {}) if isinstance(query_step, dict) else {}
        content = data_evidence.content
        columns = content.get("columns", [])
        rows = content.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise ValidationError(
                "data_evidence_invalid", "The governed query returned an invalid table contract"
            )
        safe_rows = [item for item in rows if isinstance(item, dict)]
        metric = resource.get("metric", {}) if isinstance(resource, dict) else {}
        metric_title = (
            metric.get("display_name")
            if isinstance(metric, dict) and isinstance(metric.get("display_name"), str)
            else "Governed query"
        )
        lineage = {
            "run_id": str(run.id),
            "evidence_id": str(data_evidence.id),
            "content_fingerprint": data_evidence.content_fingerprint,
            "semantic": resource,
        }
        common = {
            "organization_id": run.organization_id,
            "workspace_id": thread.workspace_id,
            "run_id": run.id,
            "classification": data_evidence.classification,
            "acl": {"users": [str(turn.created_by)]},
        }
        artifacts: list[Artifact] = []
        sql = payload.get("sql") if isinstance(payload, dict) else None
        if isinstance(sql, str):
            artifacts.append(
                Artifact(
                    **common,
                    kind=ArtifactKind.SQL,
                    title=f"{metric_title} · validated SQL",
                    media_type="text/sql",
                    inline_content={
                        "sql": sql,
                        "parameters": payload.get("parameters", []),
                        "parameter_types": payload.get("parameter_types", []),
                        "validation": resource.get("validation", {}),
                    },
                    lineage={
                        **lineage,
                        "query_fingerprint": hashlib.sha256(sql.encode()).hexdigest(),
                    },
                )
            )
        artifacts.append(
            Artifact(
                **common,
                kind=ArtifactKind.TABLE,
                title=f"{metric_title} · result table",
                media_type="application/vnd.obsion.table+json",
                inline_content={
                    "columns": [str(item) for item in columns],
                    "rows": safe_rows,
                    "row_count": int(content.get("row_count", len(safe_rows))),
                    "metric": metric,
                },
                lineage=lineage,
            )
        )
        chart = self._chart_contract([str(item) for item in columns], safe_rows)
        if chart is not None:
            chart["usermeta"] = {
                "obsion": {
                    "metric": metric,
                    "evidence_id": str(data_evidence.id),
                    "query_fingerprint": hashlib.sha256(sql.encode()).hexdigest()
                    if isinstance(sql, str)
                    else None,
                }
            }
            artifacts.append(
                Artifact(
                    **common,
                    kind=ArtifactKind.CHART,
                    title=f"{metric_title} · chart",
                    media_type="application/vnd.vegalite.v5+json",
                    inline_content=chart,
                    lineage=lineage,
                )
            )
        return artifacts

    def _workspace_dashboard_artifact(
        self,
        run: Run,
        turn: Turn,
        thread: Thread,
        result_artifacts: list[Artifact],
    ) -> Artifact | None:
        if any(item.kind == ArtifactKind.DASHBOARD for item in result_artifacts):
            return None
        charts = [item for item in result_artifacts if item.kind == ArtifactKind.CHART]
        if not charts:
            return None
        panel_kinds = {ArtifactKind.CHART, ArtifactKind.TABLE, ArtifactKind.SQL}
        panels = [
            {
                "artifact_id": str(item.id),
                "kind": item.kind,
                "title": item.title,
            }
            for item in result_artifacts
            if item.kind in panel_kinds
        ]
        return Artifact(
            organization_id=run.organization_id,
            workspace_id=thread.workspace_id,
            run_id=run.id,
            kind=ArtifactKind.DASHBOARD,
            title="Workspace dashboard",
            media_type="application/vnd.obsion.dashboard+json",
            inline_content={
                "panels": panels,
                "chart_artifact_ids": [str(item.id) for item in charts],
                "table_artifact_ids": [
                    str(item.id) for item in result_artifacts if item.kind == ArtifactKind.TABLE
                ],
                "sql_artifact_ids": [
                    str(item.id) for item in result_artifacts if item.kind == ArtifactKind.SQL
                ],
                "source": "workspace-dashboard",
            },
            classification=charts[0].classification,
            acl={"users": [str(turn.created_by)]},
            lineage={
                "run_id": str(run.id),
                "source": "workspace-dashboard",
                "chart_artifact_ids": [str(item.id) for item in charts],
            },
        )

    def _workspace_report_artifact(
        self,
        run: Run,
        turn: Turn,
        thread: Thread,
        *,
        answer: Artifact,
        result_artifacts: list[Artifact],
        citations: list[dict[str, Any]],
        incident_fusion: IncidentFusionResult | None,
    ) -> Artifact | None:
        if any(item.kind == ArtifactKind.REPORT for item in result_artifacts):
            return None
        if not citations and not result_artifacts and incident_fusion is None:
            return None
        content = dict(answer.inline_content or {})
        return Artifact(
            organization_id=run.organization_id,
            workspace_id=thread.workspace_id,
            run_id=run.id,
            kind=ArtifactKind.REPORT,
            title="Workspace report",
            media_type="text/markdown",
            inline_content={
                "markdown": content.get("markdown") or "",
                "verification": content.get("verification"),
                "citations": content.get("citations") or [],
                "answer_artifact_id": str(answer.id),
                "linked_artifact_ids": [str(item.id) for item in result_artifacts],
                **(
                    {"incident_fusion": content.get("incident_fusion")}
                    if content.get("incident_fusion") is not None
                    else {}
                ),
            },
            classification=answer.classification,
            acl={"users": [str(turn.created_by)]},
            lineage={
                "run_id": str(run.id),
                "answer_artifact_id": str(answer.id),
                "source": "workspace-report",
            },
        )

    def _engineering_result_artifacts(
        self,
        run: Run,
        turn: Turn,
        thread: Thread,
        evidence: list[Evidence],
    ) -> list[Artifact]:
        code_evidence = [
            item
            for item in evidence
            if item.evidence_type == EvidenceType.CODE and Critic._substantive(item)
        ]
        git_evidence = [
            item
            for item in evidence
            if item.evidence_type == EvidenceType.GIT and Critic._substantive(item)
        ]
        if not code_evidence and not git_evidence:
            return []
        common = {
            "organization_id": run.organization_id,
            "workspace_id": thread.workspace_id,
            "run_id": run.id,
            "acl": {"users": [str(turn.created_by)]},
        }
        artifacts: list[Artifact] = []
        for item in code_evidence:
            rows = [row for row in (item.content.get("items") or []) if isinstance(row, dict)]
            if not rows:
                continue
            artifacts.append(
                Artifact(
                    **common,
                    kind=ArtifactKind.CODE,
                    title="Authorized code graph symbols",
                    media_type="application/vnd.obsion.code+json",
                    inline_content={
                        "symbols": [
                            {
                                "repository": row.get("repository") or item.source,
                                "path": row.get("path") or item.resource,
                                "qualified_name": row.get("qualified_name"),
                                "kind": row.get("kind"),
                                "commit_id": row.get("commit_id"),
                            }
                            for row in rows[:200]
                        ],
                        "count": int(item.content.get("count", len(rows))),
                    },
                    classification=item.classification,
                    lineage={
                        "run_id": str(run.id),
                        "evidence_id": str(item.id),
                        "content_fingerprint": item.content_fingerprint,
                    },
                )
            )
        for item in git_evidence:
            rows = [row for row in (item.content.get("items") or []) if isinstance(row, dict)]
            if not rows:
                continue
            artifacts.append(
                Artifact(
                    **common,
                    kind=ArtifactKind.DIFF,
                    title="Authorized Git diff",
                    media_type="application/vnd.obsion.diff+json",
                    inline_content={
                        "commits": [
                            {
                                "repository": row.get("repository") or item.source,
                                "commit_id": row.get("commit_id"),
                                "files": (row.get("attributes") or {}).get("files")
                                if isinstance(row.get("attributes"), dict)
                                else row.get("files"),
                                "patch": (row.get("attributes") or {}).get("patch")
                                if isinstance(row.get("attributes"), dict)
                                else row.get("patch"),
                            }
                            for row in rows[:100]
                        ],
                        "count": int(item.content.get("count", len(rows))),
                    },
                    classification=item.classification,
                    lineage={
                        "run_id": str(run.id),
                        "evidence_id": str(item.id),
                        "content_fingerprint": item.content_fingerprint,
                    },
                )
            )
        if artifacts:
            source = code_evidence[0] if code_evidence else git_evidence[0]
            artifacts.append(
                Artifact(
                    **common,
                    kind=ArtifactKind.REPORT,
                    title="Engineering evidence report",
                    media_type="text/markdown",
                    inline_content={
                        "markdown": (
                            f"本次运行收集到 {len(code_evidence)} 份 CODE Evidence、"
                            f"{len(git_evidence)} 份 GIT Evidence。"
                            "符号与 diff 均来自授权只读 Capability，仓库代码未执行。"
                        ),
                        "code_evidence_ids": [str(item.id) for item in code_evidence],
                        "git_evidence_ids": [str(item.id) for item in git_evidence],
                    },
                    classification=source.classification,
                    lineage={"run_id": str(run.id)},
                )
            )
        return artifacts

    @staticmethod
    def _chart_contract(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not columns or not rows:
            return None
        numeric_field = next(
            (
                column
                for column in reversed(columns)
                if any(HarnessRuntime._numeric_value(row.get(column)) is not None for row in rows)
            ),
            None,
        )
        if numeric_field is None:
            return None
        category_field = next((column for column in columns if column != numeric_field), None)
        encoding: dict[str, Any] = {"y": {"field": numeric_field, "type": "quantitative"}}
        mark: str | dict[str, Any] = "bar"
        if category_field:
            temporal = any(
                token in category_field.casefold()
                for token in ("date", "time", "day", "week", "month", "hour")
            )
            encoding["x"] = {
                "field": category_field,
                "type": "temporal" if temporal else "nominal",
                "sort": None,
            }
            if temporal:
                mark = {"type": "line", "point": True}
        else:
            mark = "text"
            encoding["text"] = {"field": numeric_field, "type": "quantitative"}
        chart_rows = []
        for row in rows:
            normalized = dict(row)
            numeric_value = HarnessRuntime._numeric_value(row.get(numeric_field))
            if numeric_value is not None:
                normalized[numeric_field] = numeric_value
            chart_rows.append(normalized)
        return {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "description": "Governed result visualization derived from cited data evidence",
            "data": {"values": chart_rows},
            "mark": (
                {**mark, "tooltip": True}
                if isinstance(mark, dict)
                else {"type": mark, "tooltip": True}
            ),
            "encoding": encoding,
        }

    @staticmethod
    def _numeric_value(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    async def _prompt_segments(self, session: AsyncSession, run: Run) -> list[ContextSegment]:
        loaded = await load_pinned_templates(session, run.organization_id, run.prompt_pins or [])
        available = governed_prompt_values(run.plan if isinstance(run.plan, dict) else {})
        segments: list[ContextSegment] = []
        for index, (pin, template, schema) in enumerate(loaded):
            declared = (
                {
                    key: value
                    for key, value in available.items()
                    if key in schema.get("properties", {})
                }
                if isinstance(schema.get("properties"), dict)
                else {}
            )
            rendered = render_prompt_template(template, schema, declared)
            name = str(pin.get("name") or SYSTEM_POLICY_PROMPT_NAME)
            system = name == SYSTEM_POLICY_PROMPT_NAME
            segments.append(
                ContextSegment(
                    TrustLevel.SYSTEM if system else TrustLevel.AGENT,
                    rendered,
                    name if not system else "platform-policy",
                    1000 if system else 890,
                    100 + index,
                )
            )
        return segments

    async def _synthesize(
        self,
        session: AsyncSession,
        run: Run,
        turn: Turn,
        agent_version: AgentVersion,
        agent_definition: AgentDefinition,
        evidence: list[Evidence],
        memory_snapshots: list[RunMemorySnapshot],
        conversation_snapshots: list[RunConversationSnapshot],
    ) -> tuple[str, list[dict[str, Any]]]:
        if self._evidence_free_response_allowed(run):
            return "你好，我在。你可以继续描述要处理的问题，我会按受控流程推进。", []
        if run.plan.get("route") == "INCIDENT":
            # IncidentAgent must remain useful when no model is configured.  The
            # deterministic fusion path also prevents a model from turning one
            # provider signal into a causal conclusion or an unlinked Claim.
            return self._incident_evidence_answer(evidence)
        memory_payload = [
            {
                "id": str(item.id),
                "scope": item.scope,
                "content": item.content,
                "content_fingerprint": item.content_fingerprint,
                "sensitivity": item.sensitivity,
                "captured_at": item.captured_at.isoformat(),
            }
            for item in memory_snapshots
        ]
        if run.model_profile_id is not None:
            remaining_input_tokens = run.max_input_tokens - run.input_tokens
            remaining_output_tokens = run.max_output_tokens - run.output_tokens
            remaining_cost = Decimal(run.max_cost_amount) - Decimal(run.cost_amount)
            if remaining_input_tokens <= 0:
                raise BudgetExceededError("input_tokens", run.max_input_tokens)
            if remaining_output_tokens <= 0:
                raise BudgetExceededError("output_tokens", run.max_output_tokens)
            if remaining_cost <= 0:
                raise BudgetExceededError("cost_amount", run.max_cost_amount)
            skill_context = run.plan.get("skill", {})
            if not isinstance(skill_context, dict):
                skill_context = {}
            segments = [
                *await self._prompt_segments(session, run),
                ContextSegment(
                    TrustLevel.AGENT,
                    json.dumps(agent_version.spec, ensure_ascii=False),
                    agent_definition.name,
                    900,
                    200,
                ),
                ContextSegment(
                    TrustLevel.AGENT,
                    json.dumps(skill_context, ensure_ascii=False),
                    str(skill_context.get("name", "skill-policy")),
                    880,
                    250,
                ),
                *workspace_context_segments(
                    run.workspace_context if isinstance(run.workspace_context, dict) else {}
                ),
                ContextSegment(
                    TrustLevel.USER,
                    turn.sanitized_input,
                    "current-user",
                    850,
                    700,
                ),
                *evidence_context_segments(evidence),
            ]
            segments.extend(self._conversation_segments(run, turn, conversation_snapshots))
            if memory_payload:
                segments.append(
                    ContextSegment(
                        TrustLevel.UNTRUSTED_DATA,
                        json.dumps(memory_payload, ensure_ascii=False, default=str),
                        "governed-memory-snapshot",
                        700,
                        850,
                    )
                )
            try:
                result = await self.models.complete(
                    session,
                    organization_id=run.organization_id,
                    run_id=run.id,
                    step_id=None,
                    profile_id=run.model_profile_id,
                    messages=self._pin_context_budget(
                        run,
                        ContextBuilder(
                            character_budget=max(512, int(remaining_input_tokens * 0.8))
                        ).pack(segments),
                    ),
                    classification=self._highest_classification(
                        evidence,
                        memory_snapshots,
                        conversation_snapshots,
                    ),
                    json_mode=True,
                    max_input_tokens=remaining_input_tokens,
                    max_output_tokens=remaining_output_tokens,
                    max_cost_amount=remaining_cost,
                )
                run.input_tokens += result.input_tokens
                run.output_tokens += result.output_tokens
                run.cost_amount = Decimal(run.cost_amount) + result.cost_amount
                parsed = json.loads(result.content)
                answer = parsed.get("answer")
                claims = parsed.get("claims")
                if isinstance(answer, str) and isinstance(claims, list):
                    normalized = self._normalize_claims(claims, evidence)
                    if normalized:
                        return answer, normalized
            except (ModelUnavailableError, json.JSONDecodeError, TypeError, ValueError):
                pass
        return self._evidence_only_answer(run, evidence)

    def _incident_evidence_answer(
        self, evidence: list[Evidence]
    ) -> tuple[str, list[dict[str, Any]]]:
        fusion = self.incident_fusion.fuse(evidence)
        lines = [
            "已完成只读事故调查。以下是基于当前授权证据的候选根因，按支持度排序；候选不等于已确认结论。",
            "",
            f"证据覆盖：{', '.join(fusion.evidence_type_coverage) or '无'}；"
            f"候选根因 Top 1/Top 3：{len(fusion.candidates)}/{min(3, len(fusion.candidates))}。",
        ]
        claims: list[dict[str, Any]] = []
        if fusion.candidates:
            lines.extend(["", "### 候选根因（Top 3）"])
            for candidate in fusion.candidates:
                evidence_label = ", ".join(candidate.evidence_types)
                lines.append(
                    f"{candidate.rank}. {candidate.statement} "
                    f"支持度 {candidate.score:.2f}；Evidence 类型：{evidence_label}；"
                    f"Evidence IDs：{', '.join(candidate.evidence_ids)}。"
                )
                claims.append(
                    {
                        "statement": candidate.statement,
                        "evidence_ids": list(candidate.evidence_ids),
                        "confidence": candidate.score,
                    }
                )
        else:
            lines.extend(
                [
                    "",
                    "当前证据没有形成跨类型关联，因此不发布候选根因。请补充指标、发布或日志证据后重试。",
                ]
            )
        if fusion.timeline:
            lines.extend(["", "### 证据时间线"])
            for item in fusion.timeline[:12]:
                lines.append(
                    f"- {item['observed_at']} · {item['type']} · {item['source']} "
                    f"（Evidence `{item['evidence_id']}`）"
                )
        if fusion.conflicts:
            lines.extend(["", "### 未解决冲突"])
            for conflict in fusion.conflicts[:5]:
                lines.append(f"- {conflict.get('reason', 'Evidence signals conflict')}.")
        return "\n".join(lines), claims

    @staticmethod
    def _highest_classification(
        evidence: list[Evidence],
        memory_snapshots: list[RunMemorySnapshot] | None = None,
        conversation_snapshots: list[RunConversationSnapshot] | None = None,
    ) -> Classification:
        order = {
            Classification.PUBLIC: 0,
            Classification.INTERNAL: 1,
            Classification.CONFIDENTIAL: 2,
            Classification.RESTRICTED: 3,
        }
        classifications = [item.classification for item in evidence]
        classifications.extend(item.sensitivity for item in memory_snapshots or [])
        classifications.extend(item.classification for item in conversation_snapshots or [])
        if not classifications:
            return Classification.INTERNAL
        return max(classifications, key=order.__getitem__)

    @staticmethod
    def _normalize_claims(claims: list[Any], evidence: list[Evidence]) -> list[dict[str, Any]]:
        allowed = {
            str(item.id)
            for item in evidence
            if not (
                isinstance(item.content.get("hits"), list)
                and not item.content["hits"]
                and item.content.get("count") == 0
            )
        }
        normalized: list[dict[str, Any]] = []
        for claim in claims[:20]:
            if not isinstance(claim, dict) or not isinstance(claim.get("statement"), str):
                continue
            linked = [
                str(value) for value in claim.get("evidence_ids", []) if str(value) in allowed
            ]
            if not linked:
                continue
            normalized.append(
                {
                    "statement": claim["statement"],
                    "evidence_ids": linked,
                    "confidence": float(claim.get("confidence", 0.8)),
                }
            )
        return normalized

    @staticmethod
    def _knowledge_citations(
        claims: list[dict[str, Any]], evidence: list[Evidence]
    ) -> list[dict[str, Any]]:
        evidence_by_id = {str(item.id): item for item in evidence}
        referenced_ids = [
            evidence_id
            for claim in claims
            for evidence_id in claim.get("evidence_ids", [])
            if evidence_id in evidence_by_id
        ]
        if not referenced_ids:
            referenced_ids = [
                str(item.id)
                for item in evidence
                if not (
                    isinstance(item.content.get("hits"), list)
                    and not item.content["hits"]
                    and item.content.get("count") == 0
                )
            ]
        citations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for evidence_id in referenced_ids:
            item = evidence_by_id[evidence_id]
            hits = item.content.get("hits")
            if isinstance(hits, list) and hits:
                candidates = [hit for hit in hits if isinstance(hit, dict)]
            else:
                candidates = [item.content]
            for candidate in candidates:
                chunk_id = str(candidate.get("chunk_id", ""))
                key = (evidence_id, chunk_id)
                if key in seen:
                    continue
                seen.add(key)
                citation = {
                    "label": f"[{len(citations) + 1}]",
                    "evidence_id": evidence_id,
                    "source": str(candidate.get("source", item.source)),
                    "resource": item.resource,
                    "title": str(candidate.get("title", "授权文档")),
                    "version": candidate.get("version"),
                    "chunk_id": candidate.get("chunk_id"),
                    "external_id": candidate.get("external_id"),
                    "revision_id": candidate.get("revision_id"),
                    "connector_name": candidate.get("connector_name"),
                    "operation": candidate.get("operation"),
                }
                citations.append(citation)
                if len(citations) >= 8:
                    return citations
        return citations

    @staticmethod
    def _append_knowledge_citations(answer: str, citations: list[dict[str, Any]]) -> str:
        lines = [answer.rstrip(), "", "### 引用"]
        for citation in citations:
            version = citation.get("version")
            version_label = f" · v{version}" if version is not None else ""
            chunk_id = citation.get("chunk_id")
            chunk_label = f" · chunk {chunk_id}" if chunk_id else ""
            lines.append(
                f"- {citation['label']} **{citation['title']}** · "
                f"{citation['source']}{version_label}{chunk_label} "
                f"（Evidence `{citation['evidence_id']}`）"
            )
        return "\n".join(lines)

    @staticmethod
    def _knowledge_unknown_answer() -> str:
        return "不知道：在当前授权知识范围内没有找到足够的证据来回答这个问题。"

    @staticmethod
    def _code_unknown_answer() -> str:
        return "不知道：在当前授权代码图中没有找到足够的符号、引用或调用链证据。"

    @staticmethod
    def _code_citations(
        claims: list[dict[str, Any]], evidence: list[Evidence]
    ) -> list[dict[str, Any]]:
        evidence_by_id = {str(item.id): item for item in evidence}
        referenced_ids = [
            evidence_id
            for claim in claims
            for evidence_id in claim.get("evidence_ids", [])
            if evidence_id in evidence_by_id
        ]
        if not referenced_ids:
            referenced_ids = [
                str(item.id)
                for item in evidence
                if not (
                    isinstance(item.content.get("items"), list)
                    and not item.content["items"]
                    and item.content.get("count") == 0
                )
            ]
        citations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for evidence_id in referenced_ids:
            item = evidence_by_id[evidence_id]
            hits = item.content.get("items")
            candidates = (
                [hit for hit in hits if isinstance(hit, dict)]
                if isinstance(hits, list)
                else [item.content]
            )
            for candidate in candidates:
                symbol_id = str(candidate.get("symbol_id") or candidate.get("qualified_name") or "")
                key = (evidence_id, symbol_id)
                if key in seen:
                    continue
                seen.add(key)
                citations.append(
                    {
                        "label": f"[{len(citations) + 1}]",
                        "evidence_id": evidence_id,
                        "repository": str(candidate.get("repository", item.source)),
                        "path": str(candidate.get("path", item.resource)),
                        "qualified_name": str(candidate.get("qualified_name", "")),
                        "commit_id": candidate.get("commit_id"),
                    }
                )
                if len(citations) >= 8:
                    return citations
        return citations

    @staticmethod
    def _append_code_citations(answer: str, citations: list[dict[str, Any]]) -> str:
        lines = [answer.rstrip(), "", "### 代码证据"]
        for citation in citations:
            commit = citation.get("commit_id")
            commit_label = f" · {commit}" if commit else ""
            lines.append(
                f"- {citation['label']} `{citation['qualified_name']}` · "
                f"{citation['repository']}:{citation['path']}{commit_label} "
                f"（Evidence `{citation['evidence_id']}`）"
            )
        return "\n".join(lines)

    @staticmethod
    def _evidence_only_answer(
        run: Run, evidence: list[Evidence]
    ) -> tuple[str, list[dict[str, Any]]]:
        route = run.plan.get("route")
        claims: list[dict[str, Any]] = []
        lines = ["已完成受控检索，以下内容严格来自当前可访问证据。"]
        if route in {"KNOWLEDGE", "SUPPORT"}:
            hits = [
                hit
                for item in evidence
                for hit in item.content.get("hits", [])
                if isinstance(hit, dict)
            ]
            for hit in hits[:5]:
                snippet = str(hit.get("content", "")).strip().replace("\n", " ")[:280]
                title = hit.get("title", "未命名文档")
                lines.append(f"- **{title}**：{snippet}")
            attachments = [item for item in evidence if item.source == "workspace-artifact"]
            for item in attachments[:3]:
                title = item.content.get("title", "附件")
                snippet = str(item.content.get("text", "")).strip().replace("\n", " ")[:280]
                lines.append(f"- **{title}**：{snippet}")
            if hits or attachments:
                claims.append(
                    {
                        "statement": (
                            f"找到 {len(hits)} 条经过权限过滤的知识片段，并读取 "
                            f"{len(attachments)} 个已授权附件。"
                        ),
                        "evidence_ids": [str(item.id) for item in evidence],
                        "confidence": 0.9,
                    }
                )
            else:
                lines = [HarnessRuntime._knowledge_unknown_answer()]
        elif route == "ENGINEERING":
            items = [
                hit
                for item in evidence
                for hit in item.content.get("items", [])
                if isinstance(hit, dict)
            ]
            for hit in items[:8]:
                lines.append(
                    f"- **{hit.get('qualified_name') or hit.get('name')}** · "
                    f"{hit.get('path')} ({hit.get('kind')})"
                )
            if items:
                claims.append(
                    {
                        "statement": f"Code Graph 返回 {len(items)} 个已授权符号或关系。",
                        "evidence_ids": [str(item.id) for item in evidence],
                        "confidence": 0.9,
                    }
                )
            else:
                lines = [HarnessRuntime._code_unknown_answer()]
        elif route in {"DATA", "ANALYTICS"}:
            result = evidence[0].content
            semantic = evidence[0].lineage.get("request_resource", {})
            metric = semantic.get("metric", {}) if isinstance(semantic, dict) else {}
            if isinstance(metric, dict) and metric.get("display_name"):
                version = metric.get("version")
                version_label = f" v{version}" if version is not None else ""
                lines.append(f"指标定义：{metric['display_name']}{version_label}（已治理）")
            lines.append(f"查询返回 {result.get('row_count', 0)} 行受控结果。")
            lines.append("可在结果表格与 SQL Evidence 中继续查看明细和指标口径。")
            claims.append(
                {
                    "statement": f"受控查询返回 {result.get('row_count', 0)} 行结果。",
                    "evidence_ids": [str(evidence[0].id)],
                    "confidence": 1.0,
                }
            )
        else:
            for item in evidence:
                lines.append(f"- 已收集 {item.evidence_type.value} 证据：{item.source}")
            lines.append("当前为证据汇总；未配置可用推理模型时不自动断言根因。")
            claims.append(
                {
                    "statement": f"本次调查收集到 {len(evidence)} 项可审计证据。",
                    "evidence_ids": [str(item.id) for item in evidence],
                    "confidence": 1.0,
                }
            )
        return "\n\n".join(lines), claims

    async def _cancel(self, session: AsyncSession, run: Run) -> None:
        if not is_terminal(run.status):
            validate_run_transition(run.status, RunStatus.CANCELLED)
            run.status = RunStatus.CANCELLED
            run.completed_at = utc_now()
            run.lease_owner = None
            run.lease_expires_at = None
            await self.events.append(session, self._event(run, "run.cancelled", {}))
        if run.status == RunStatus.CANCELLED:
            await cancel_active_run_steps(
                session,
                run.organization_id,
                run.id,
                completed_at=run.completed_at or utc_now(),
            )

    async def _fail(self, organization_id: UUID, run_id: UUID, exc: Exception) -> None:
        async with self.database.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run)
                .where(Run.id == run_id, Run.organization_id == organization_id)
                .with_for_update()
            )
            if run is None or is_terminal(run.status):
                return
            validate_run_transition(run.status, RunStatus.FAILED)
            run.status = RunStatus.FAILED
            run.error_code = exc.code if isinstance(exc, ObsionError) else "internal_error"
            run.error_message = (
                exc.message if isinstance(exc, ObsionError) else "The run failed unexpectedly"
            )
            failed_at = utc_now()
            run.completed_at = failed_at
            run.lease_owner = None
            run.lease_expires_at = None
            await self.events.append(
                session,
                self._event(
                    run,
                    "run.failed",
                    {"error_code": run.error_code, "message": run.error_message},
                ),
            )
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=run.organization_id,
                    correlation_id=run.id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    action="run.fail",
                    resource_type="run",
                    resource_id=str(run.id),
                    outcome="FAILED",
                    metadata={"error_code": run.error_code},
                    latency_ms=(
                        max(
                            0,
                            int((failed_at - ensure_utc(run.started_at)).total_seconds() * 1000),
                        )
                        if run.started_at is not None
                        else None
                    ),
                    agent_version_id=run.agent_version_id,
                    model_profile_id=run.model_profile_id,
                    resource={"run_id": str(run.id)},
                    result_classification=Classification.INTERNAL,
                ),
            )

    def _conversation_segments(
        self,
        run: Run,
        turn: Turn,
        conversation_snapshots: list[RunConversationSnapshot],
    ) -> list[ContextSegment]:
        compacted = ConversationCompactor().compact(
            conversation_turns_from_snapshots(conversation_snapshots)
        )
        if not run.conversation_compact:
            run.conversation_compact = compacted.as_dict()
            conversation_compact_counter.add(
                1,
                {
                    "method": compacted.method,
                    "summarized": str(compacted.summarized_turns > 0).lower(),
                },
            )
        segments: list[ContextSegment] = []
        if compacted.summary_segment is not None:
            segments.append(compacted.summary_segment)
        recent_by_id = {item.source_turn_id: item for item in compacted.recent}
        for index, snapshot in enumerate(conversation_snapshots):
            if snapshot.source_turn_id not in recent_by_id:
                continue
            user_trust = (
                TrustLevel.USER
                if snapshot.source_principal_id == turn.created_by
                else TrustLevel.UNTRUSTED_DATA
            )
            segments.append(
                ContextSegment(
                    user_trust,
                    snapshot.user_content,
                    f"thread-turn:{snapshot.source_turn_id}",
                    600,
                    300 + index * 2,
                )
            )
            if snapshot.assistant_content:
                segments.append(
                    ContextSegment(
                        TrustLevel.ASSISTANT,
                        snapshot.assistant_content,
                        f"run-answer:{snapshot.source_run_id}",
                        600,
                        301 + index * 2,
                    )
                )
        return segments

    @staticmethod
    def _pin_context_budget(run: Run, pack: ContextPack) -> list[dict[str, str]]:
        if not run.context_budget:
            run.context_budget = pack.as_dict()
            for decision in pack.decisions:
                context_budget_counter.add(1, {"action": decision.action.value})
        return pack.messages

    @staticmethod
    def _event(run: Run, name: str, payload: dict[str, Any]) -> EventDraft:
        return EventDraft(
            name=name,
            aggregate_type="run",
            aggregate_id=run.id,
            organization_id=run.organization_id,
            correlation_id=run.id,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            run_id=run.id,
            payload=jsonable_encoder(payload),
        )

    @staticmethod
    def _intent_event_payload(intent: dict[str, Any]) -> dict[str, Any]:
        # The persisted intent also carries internal route metadata. The v1 event
        # contract intentionally exposes only the public Understanding projection.
        fields = {
            "comparison",
            "dimensions",
            "domain",
            "intent",
            "metrics",
            "need_data",
            "need_root_cause",
            "question",
            "risk",
            "route",
            "time_range",
        }
        return {key: value for key, value in intent.items() if key in fields}

    @staticmethod
    def _plan_event_payload(plan: dict[str, Any]) -> dict[str, Any]:
        # Agent and pinned Skill snapshots are API/replay metadata, not part of the
        # frozen v1 plan.created payload schema.
        fields = {"route", "steps", "required_evidence", "verification"}
        return {key: value for key, value in plan.items() if key in fields}
