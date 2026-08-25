import asyncio
import hashlib
import json
import math
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    Claim,
    ClaimEvidence,
    DataSource,
    Evidence,
    Run,
    RunStep,
    Thread,
    Turn,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    ActorType,
    ArtifactKind,
    Classification,
    EvidenceType,
    RunStatus,
    StepKind,
    StepStatus,
    VerificationStatus,
)
from obsion.domain.run_state import is_terminal, validate_run_transition
from obsion.harness.critic import Critic
from obsion.harness.planner import Planner
from obsion.harness.replay import RunReplayService
from obsion.harness.understanding import UnderstandingEngine
from obsion.knowledge.parsers import parse_document
from obsion.model_gateway.context import ContextBuilder, ContextSegment, TrustLevel
from obsion.model_gateway.gateway import ModelGateway, ModelUnavailableError
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text
from obsion.telemetry import run_counter, tracer


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
        self.understanding = UnderstandingEngine()
        self.planner = Planner()
        self.critic = Critic()
        self.data = DataIntelligenceService(settings)
        self.replays = RunReplayService()

    async def execute(self, organization_id: UUID, run_id: UUID) -> None:
        with tracer.start_as_current_span("obsion.run") as span:
            span.set_attribute("obsion.run.id", str(run_id))
            try:
                replay = await self._materialize_replay(organization_id, run_id)
                if replay:
                    span.set_attribute("obsion.run.replay", True)
                    run_counter.add(1, {"status": "REPLAYED"})
                    return
                await self._prepare(organization_id, run_id)
                should_continue = await self._execute_steps(organization_id, run_id)
                if should_continue:
                    while await self._replan_transient_failures(organization_id, run_id):
                        should_continue = await self._execute_steps(organization_id, run_id)
                        if not should_continue:
                            run_counter.add(1, {"status": "WAITING"})
                            return
                    await self._respond(organization_id, run_id)
                    run_counter.add(1, {"status": "COMPLETED"})
                else:
                    run_counter.add(1, {"status": "WAITING"})
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("error.type", type(exc).__name__)
                run_counter.add(1, {"status": "FAILED"})
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
    ) -> tuple[Run, Turn, Thread, Principal, AgentVersion, AgentDefinition]:
        row = (
            await session.execute(
                select(Run, Turn, Thread, AgentVersion, AgentDefinition)
                .join(Turn, Turn.id == Run.turn_id)
                .join(Thread, Thread.id == Turn.thread_id)
                .join(AgentVersion, AgentVersion.id == Run.agent_version_id)
                .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
                .where(Run.id == run_id, Run.organization_id == organization_id)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Run", run_id)
        run, turn, thread, agent_version, agent_definition = row._tuple()
        principal = await load_principal_by_id(session, organization_id, turn.created_by)
        return run, turn, thread, principal, agent_version, agent_definition

    async def _prepare(self, organization_id: UUID, run_id: UUID) -> None:
        async with self.database.sessions() as session, session.begin():
            run, turn, _, principal, _, _ = await self._load_context(
                session, organization_id, run_id
            )
            if is_terminal(run.status) or run.plan:
                return
            if run.cancellation_requested_at:
                await self._cancel(session, run)
                return
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
            compiled_payload: dict[str, Any] | None = None
            if understanding["route"] == "DATA":
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
            plan = self.planner.create(understanding, compiled_data_query=compiled_payload)
            run.intent = jsonable_encoder(understanding)
            run.plan = jsonable_encoder(plan.as_dict())
            run.step_count = 2
            now = utc_now()
            session.add_all(
                [
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=1,
                        name="Understand request",
                        kind=StepKind.UNDERSTAND,
                        status=StepStatus.COMPLETED,
                        input_payload={},
                        started_at=now,
                        completed_at=now,
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=2,
                        name="Create governed execution plan",
                        kind=StepKind.PLAN,
                        status=StepStatus.COMPLETED,
                        input_payload={},
                        started_at=now,
                        completed_at=now,
                    ),
                ]
            )
            for ordinal, step in enumerate(plan.steps, start=3):
                session.add(
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=ordinal,
                        name=step.name,
                        kind=StepKind.CAPABILITY,
                        status=StepStatus.PENDING,
                        depends_on=[value + 2 for value in step.depends_on],
                        input_payload={
                            "capability": step.capability,
                            "payload": jsonable_encoder(step.payload),
                            "resource": jsonable_encoder(step.resource),
                            "environment": step.environment,
                        },
                        max_retries=1,
                    )
                )
            await self._ingest_attachments(session, run, turn)
            await self.events.append(
                session,
                self._event(
                    run,
                    "context.resolved",
                    {
                        "context_refs": turn.context_refs,
                        "attachment_refs": turn.attachment_refs,
                    },
                ),
            )
            await self.events.append(
                session,
                self._event(run, "intent.detected", run.intent),
            )
            await self.events.append(
                session,
                self._event(run, "plan.created", run.plan),
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
            evidence = Evidence(
                organization_id=run.organization_id,
                run_id=run.id,
                evidence_type=EvidenceType.DOCUMENT,
                source="workspace-artifact",
                resource=f"artifact:{artifact.id}",
                observed_at=artifact.updated_at,
                ingested_at=utc_now(),
                content={"title": artifact.title, "text": normalized},
                content_fingerprint=hashlib.sha256(normalized.encode()).hexdigest(),
                confidence=1.0,
                classification=artifact.classification,
                permissions=["artifact.read"],
                lineage={
                    "artifact_id": str(artifact.id),
                    "checksum_sha256": artifact.checksum_sha256,
                },
            )
            session.add(evidence)
            await session.flush()
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
                    session, organization_id, run_id
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
                            RunStep.kind == StepKind.CAPABILITY,
                        )
                        .order_by(RunStep.ordinal)
                    )
                )
                if len(all_steps) + 2 > run.max_steps:
                    raise BudgetExceededError("steps", run.max_steps)
                active = [
                    step
                    for step in all_steps
                    if step.status
                    in {StepStatus.PENDING, StepStatus.WAITING_APPROVAL, StepStatus.RUNNING}
                ]
                if not active:
                    return True
                status_by_ordinal = {step.ordinal: step.status for step in all_steps}
                for step in active:
                    dependency_states = [
                        status_by_ordinal.get(ordinal) for ordinal in step.depends_on
                    ]
                    if any(
                        state in {StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED}
                        for state in dependency_states
                    ):
                        step.status = StepStatus.SKIPPED
                        step.error_code = "dependency_failed"
                        step.completed_at = utc_now()
                ready = [
                    step
                    for step in active
                    if step.status
                    in {StepStatus.PENDING, StepStatus.WAITING_APPROVAL, StepStatus.RUNNING}
                    and all(
                        status_by_ordinal.get(ordinal) == StepStatus.COMPLETED
                        for ordinal in step.depends_on
                    )
                ]
                if not ready:
                    if any(step.status == StepStatus.SKIPPED for step in active):
                        continue
                    raise ValidationError(
                        "plan_dependency_deadlock",
                        "The execution plan contains unresolved or cyclic dependencies",
                    )
                if run.step_count + len(ready) > run.max_steps:
                    raise BudgetExceededError("steps", run.max_steps)
                jobs: list[tuple[UUID, dict[str, Any], UUID | None, UUID | None, str]] = []
                for step in ready:
                    step.status = StepStatus.RUNNING
                    step.started_at = step.started_at or utc_now()
                    run.step_count += 1
                    jobs.append(
                        (
                            step.id,
                            dict(step.input_payload),
                            run.agent_version_id,
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
                        capability_version_id,
                        agent_name,
                    )
                    for (
                        step_id,
                        payload,
                        agent_version_id,
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

    async def _invoke_step(
        self,
        organization_id: UUID,
        run_id: UUID,
        step_id: UUID,
        payload: dict[str, Any],
        principal: Principal,
        agent_version_id: UUID | None,
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

    async def _respond(self, organization_id: UUID, run_id: UUID) -> None:
        async with self.database.sessions() as session, session.begin():
            run, turn, thread, _, agent_version, agent_definition = await self._load_context(
                session, organization_id, run_id
            )
            if is_terminal(run.status):
                return
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
            if not evidence:
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
            )
            required_types = tuple(run.plan.get("required_evidence", []))
            critic = self.critic.verify(
                evidence,
                required_types=required_types,
                claims=claims,
            )
            verification_status = (
                VerificationStatus.VERIFIED if critic.verified else VerificationStatus.PARTIAL
            )
            claim_models: list[Claim] = []
            evidence_by_id = {str(item.id): item for item in evidence}
            for ordinal, item in enumerate(claims, start=1):
                claim = Claim(
                    organization_id=organization_id,
                    run_id=run.id,
                    ordinal=ordinal,
                    statement=item["statement"],
                    confidence=min(
                        float(item.get("confidence", critic.confidence)), critic.confidence
                    ),
                    verification_status=verification_status,
                    critic_notes={"checks": critic.checks},
                    created_at=utc_now(),
                )
                session.add(claim)
                await session.flush()
                claim_models.append(claim)
                for evidence_id in item["evidence_ids"]:
                    if evidence_id in evidence_by_id:
                        session.add(
                            ClaimEvidence(
                                claim_id=claim.id,
                                evidence_id=evidence_by_id[evidence_id].id,
                            )
                        )
            result_artifacts = self._data_result_artifacts(run, turn, thread, evidence)
            session.add_all(result_artifacts)
            await session.flush()
            artifact = Artifact(
                organization_id=organization_id,
                workspace_id=thread.workspace_id,
                run_id=run.id,
                kind=ArtifactKind.TEXT,
                title="Obsion answer",
                media_type="text/markdown",
                inline_content={
                    "markdown": answer,
                    "verification": jsonable_encoder(asdict(critic)),
                    "claim_ids": [str(item.id) for item in claim_models],
                },
                classification=self._highest_classification(evidence),
                acl={"users": [str(turn.created_by)]},
                lineage={
                    "run_id": str(run.id),
                    "result_artifact_ids": [str(item.id) for item in result_artifacts],
                },
            )
            session.add(artifact)
            await session.flush()
            await self.events.append(session, self._event(run, "critic.completed", asdict(critic)))
            await self.events.append(
                session,
                self._event(run, "answer.delta", {"delta": answer, "final": True}),
            )
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
            run.completed_at = utc_now()
            run.lease_owner = None
            run.lease_expires_at = None
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

    def _data_result_artifacts(
        self,
        run: Run,
        turn: Turn,
        thread: Thread,
        evidence: list[Evidence],
    ) -> list[Artifact]:
        if run.plan.get("route") != "DATA":
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
                },
                lineage=lineage,
            )
        )
        chart = self._chart_contract([str(item) for item in columns], safe_rows)
        if chart is not None:
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
        mark = "bar"
        if category_field:
            encoding["x"] = {"field": category_field, "type": "nominal", "sort": None}
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
            "mark": {"type": mark, "tooltip": True},
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

    async def _synthesize(
        self,
        session: AsyncSession,
        run: Run,
        turn: Turn,
        agent_version: AgentVersion,
        agent_definition: AgentDefinition,
        evidence: list[Evidence],
    ) -> tuple[str, list[dict[str, Any]]]:
        evidence_payload = [
            {
                "id": str(item.id),
                "type": item.evidence_type,
                "source": item.source,
                "resource": item.resource,
                "observed_at": item.observed_at.isoformat(),
                "content": item.content,
            }
            for item in evidence
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
            segments = [
                ContextSegment(
                    TrustLevel.SYSTEM,
                    "You are Obsion. Use only supplied evidence. Never invent facts. "
                    "Return JSON with answer and claims; every claim must cite evidence_ids.",
                    "platform-policy",
                    1000,
                ),
                ContextSegment(
                    TrustLevel.AGENT,
                    json.dumps(agent_version.spec, ensure_ascii=False),
                    agent_definition.name,
                    900,
                ),
                ContextSegment(TrustLevel.USER, turn.sanitized_input, "user", 800),
                ContextSegment(
                    TrustLevel.UNTRUSTED_DATA,
                    json.dumps(evidence_payload, ensure_ascii=False, default=str),
                    "evidence-bus",
                    700,
                ),
            ]
            try:
                result = await self.models.complete(
                    session,
                    organization_id=run.organization_id,
                    run_id=run.id,
                    step_id=None,
                    profile_id=run.model_profile_id,
                    messages=ContextBuilder(
                        character_budget=max(512, int(remaining_input_tokens * 0.8))
                    ).build(segments),
                    classification=self._highest_classification(evidence),
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

    @staticmethod
    def _highest_classification(evidence: list[Evidence]) -> Classification:
        order = {
            Classification.PUBLIC: 0,
            Classification.INTERNAL: 1,
            Classification.CONFIDENTIAL: 2,
            Classification.RESTRICTED: 3,
        }
        return max((item.classification for item in evidence), key=order.__getitem__)

    @staticmethod
    def _normalize_claims(claims: list[Any], evidence: list[Evidence]) -> list[dict[str, Any]]:
        allowed = {str(item.id) for item in evidence}
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
    def _evidence_only_answer(
        run: Run, evidence: list[Evidence]
    ) -> tuple[str, list[dict[str, Any]]]:
        route = run.plan.get("route")
        claims: list[dict[str, Any]] = []
        lines = ["已完成受控检索，以下内容严格来自当前可访问证据。"]
        if route == "KNOWLEDGE":
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
        elif route == "DATA":
            result = evidence[0].content
            lines.append(f"查询返回 {result.get('row_count', 0)} 行受控结果。")
            lines.append("可在结果表格与 SQL 证据中继续查看明细和指标口径。")
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
            run.completed_at = utc_now()
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
