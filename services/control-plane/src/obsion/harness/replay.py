import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import (
    Artifact,
    Claim,
    ClaimEvidence,
    Event,
    Evidence,
    Run,
    RunStep,
    Turn,
)
from obsion.domain.enums import ActorType, RunStatus
from obsion.domain.run_state import is_terminal, validate_run_transition
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore


@dataclass(frozen=True, slots=True)
class ReplaySnapshotResult:
    source_status: RunStatus
    snapshot_sha256: str
    step_count: int
    evidence_count: int
    claim_count: int
    artifact_count: int
    event_count: int


class RunReplayService:
    """Materialize an immutable run snapshot without crossing an external boundary."""

    def __init__(
        self,
        events: EventStore | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self.events = events or EventStore()
        self.audit = audit or AuditWriter()

    async def materialize(
        self,
        session: AsyncSession,
        organization_id: UUID,
        run_id: UUID,
    ) -> ReplaySnapshotResult:
        target = await session.scalar(
            select(Run)
            .where(Run.id == run_id, Run.organization_id == organization_id)
            .with_for_update()
        )
        if target is None:
            raise NotFoundError("Replay run", run_id)
        if target.replay_of_run_id is None:
            raise ValidationError("run_not_replay", "The run is not a replay request")
        if target.status != RunStatus.RUNNING:
            raise ConflictError(
                "replay_run_not_running",
                "A replay snapshot can only be materialized by its active worker",
                status=target.status,
            )

        source = await session.scalar(
            select(Run).where(
                Run.id == target.replay_of_run_id,
                Run.organization_id == organization_id,
            )
        )
        if source is None:
            raise NotFoundError("Replay source run", target.replay_of_run_id)
        if not is_terminal(source.status):
            raise ConflictError(
                "run_not_replayable",
                "Only a terminal run has a stable replay snapshot",
                status=source.status,
            )

        source_turn = await session.scalar(
            select(Turn).where(
                Turn.id == source.turn_id,
                Turn.organization_id == organization_id,
            )
        )
        if source_turn is None:
            raise NotFoundError("Replay source turn", source.turn_id)

        source_steps = list(
            await session.scalars(
                select(RunStep)
                .where(
                    RunStep.organization_id == organization_id,
                    RunStep.run_id == source.id,
                )
                .order_by(RunStep.ordinal)
            )
        )
        source_evidence = list(
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.organization_id == organization_id,
                    Evidence.run_id == source.id,
                )
                .order_by(Evidence.ingested_at, Evidence.id)
            )
        )
        source_claims = list(
            await session.scalars(
                select(Claim)
                .where(
                    Claim.organization_id == organization_id,
                    Claim.run_id == source.id,
                )
                .order_by(Claim.ordinal)
            )
        )
        source_artifacts = list(
            await session.scalars(
                select(Artifact)
                .where(
                    Artifact.organization_id == organization_id,
                    Artifact.run_id == source.id,
                )
                .order_by(Artifact.created_at, Artifact.id)
            )
        )
        source_events = list(
            await session.scalars(
                select(Event)
                .where(
                    Event.organization_id == organization_id,
                    Event.run_id == source.id,
                )
                .order_by(Event.sequence)
            )
        )
        source_claim_ids = [claim.id for claim in source_claims]
        source_links: list[tuple[UUID, UUID]] = (
            [
                row._tuple()
                for row in (
                    await session.execute(
                        select(ClaimEvidence.claim_id, ClaimEvidence.evidence_id).where(
                            ClaimEvidence.claim_id.in_(source_claim_ids)
                        )
                    )
                ).all()
            ]
            if source_claim_ids
            else []
        )

        step_ids = {item.id: new_id() for item in source_steps}
        evidence_ids = {item.id: new_id() for item in source_evidence}
        claim_ids = {item.id: new_id() for item in source_claims}
        artifact_ids = {item.id: new_id() for item in source_artifacts}
        replacements = {
            str(source.id): str(target.id),
            **{str(old): str(new) for old, new in step_ids.items()},
            **{str(old): str(new) for old, new in evidence_ids.items()},
            **{str(old): str(new) for old, new in claim_ids.items()},
            **{str(old): str(new) for old, new in artifact_ids.items()},
        }
        snapshot_sha256 = self._snapshot_fingerprint(
            source,
            source_steps,
            source_evidence,
            source_claims,
            source_links,
            source_artifacts,
            source_events,
        )
        now = utc_now()

        await self.events.append(
            session,
            EventDraft(
                name="run.replay.started",
                aggregate_type="run",
                aggregate_id=target.id,
                organization_id=organization_id,
                correlation_id=target.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=target.id,
                payload={
                    "source_run_id": str(source.id),
                    "source_status": source.status,
                    "snapshot_sha256": snapshot_sha256,
                },
            ),
        )

        cloned_steps: list[RunStep] = []
        for source_step in source_steps:
            capability_version_id = (
                source_step.capability_version_id
                or self._capability_version_for_step(source_step, source_evidence)
            )
            cloned_steps.append(
                RunStep(
                    id=step_ids[source_step.id],
                    organization_id=organization_id,
                    run_id=target.id,
                    ordinal=source_step.ordinal,
                    name=source_step.name,
                    kind=source_step.kind,
                    status=source_step.status,
                    depends_on=copy.deepcopy(source_step.depends_on),
                    capability_version_id=capability_version_id,
                    input_payload=self._remap(source_step.input_payload, replacements),
                    output_ref=(
                        replacements.get(source_step.output_ref, source_step.output_ref)
                        if source_step.output_ref is not None
                        else None
                    ),
                    retry_count=source_step.retry_count,
                    max_retries=source_step.max_retries,
                    started_at=source_step.started_at,
                    completed_at=source_step.completed_at,
                    error_code=source_step.error_code,
                    created_at=source_step.created_at,
                    updated_at=source_step.updated_at,
                )
            )
        session.add_all(cloned_steps)

        cloned_evidence: list[Evidence] = []
        for source_item in source_evidence:
            lineage = self._remap(source_item.lineage, replacements)
            lineage.update(
                {
                    "replay_of_evidence_id": str(source_item.id),
                    "replay_source_run_id": str(source.id),
                    "replay_snapshot_sha256": snapshot_sha256,
                }
            )
            cloned_evidence.append(
                Evidence(
                    id=evidence_ids[source_item.id],
                    organization_id=organization_id,
                    run_id=target.id,
                    step_id=(
                        step_ids.get(source_item.step_id)
                        if source_item.step_id is not None
                        else None
                    ),
                    evidence_type=source_item.evidence_type,
                    source=source_item.source,
                    resource=source_item.resource,
                    observed_at=source_item.observed_at,
                    ingested_at=source_item.ingested_at,
                    content=copy.deepcopy(source_item.content),
                    content_fingerprint=source_item.content_fingerprint,
                    confidence=source_item.confidence,
                    classification=source_item.classification,
                    permissions=copy.deepcopy(source_item.permissions),
                    lineage=lineage,
                )
            )
        session.add_all(cloned_evidence)

        cloned_claims = [
            Claim(
                id=claim_ids[item.id],
                organization_id=organization_id,
                run_id=target.id,
                ordinal=item.ordinal,
                statement=item.statement,
                confidence=item.confidence,
                verification_status=item.verification_status,
                critic_notes=copy.deepcopy(item.critic_notes),
                created_at=item.created_at,
            )
            for item in source_claims
        ]
        session.add_all(cloned_claims)
        session.add_all(
            [
                ClaimEvidence(
                    claim_id=claim_ids[claim_id],
                    evidence_id=evidence_ids[evidence_id],
                )
                for claim_id, evidence_id in source_links
                if claim_id in claim_ids and evidence_id in evidence_ids
            ]
        )

        cloned_artifacts: list[Artifact] = []
        for source_artifact in source_artifacts:
            lineage = self._remap(source_artifact.lineage, replacements)
            lineage.update(
                {
                    "replay_of_artifact_id": str(source_artifact.id),
                    "replay_source_run_id": str(source.id),
                    "replay_snapshot_sha256": snapshot_sha256,
                }
            )
            cloned_artifacts.append(
                Artifact(
                    id=artifact_ids[source_artifact.id],
                    organization_id=organization_id,
                    workspace_id=source_artifact.workspace_id,
                    run_id=target.id,
                    kind=source_artifact.kind,
                    title=source_artifact.title,
                    media_type=source_artifact.media_type,
                    inline_content=self._remap(source_artifact.inline_content, replacements),
                    storage_key=source_artifact.storage_key,
                    checksum_sha256=source_artifact.checksum_sha256,
                    classification=source_artifact.classification,
                    acl=copy.deepcopy(source_artifact.acl),
                    lineage=lineage,
                    created_at=source_artifact.created_at,
                    updated_at=source_artifact.updated_at,
                )
            )
        session.add_all(cloned_artifacts)
        await session.flush()

        for source_event in source_events:
            await self.events.append(
                session,
                EventDraft(
                    name="run.replay.event",
                    aggregate_type="run",
                    aggregate_id=target.id,
                    organization_id=organization_id,
                    correlation_id=target.id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    run_id=target.id,
                    classification=source_event.classification,
                    schema_version=source_event.schema_version,
                    payload={
                        "source_event_id": str(source_event.id),
                        "source_sequence": source_event.sequence,
                        "source_name": source_event.name,
                        "source_actor_type": source_event.actor_type,
                        "source_actor_id": (
                            str(source_event.actor_id) if source_event.actor_id else None
                        ),
                        "source_created_at": source_event.created_at.isoformat(),
                        "source_payload": self._remap(source_event.payload, replacements),
                    },
                ),
            )

        target.intent = copy.deepcopy(source.intent)
        target.plan = copy.deepcopy(source.plan)
        target.agent_version_id = source.agent_version_id
        target.model_profile_id = source.model_profile_id
        target.max_steps = source.max_steps
        target.timeout_seconds = source.timeout_seconds
        target.max_input_tokens = source.max_input_tokens
        target.max_output_tokens = source.max_output_tokens
        target.max_cost_amount = source.max_cost_amount
        target.step_count = source.step_count
        target.input_tokens = source.input_tokens
        target.output_tokens = source.output_tokens
        target.cost_amount = source.cost_amount
        target.error_code = source.error_code
        target.error_message = source.error_message
        validate_run_transition(target.status, source.status)
        target.status = source.status
        target.completed_at = now
        target.lease_owner = None
        target.lease_expires_at = None

        result = ReplaySnapshotResult(
            source_status=source.status,
            snapshot_sha256=snapshot_sha256,
            step_count=len(source_steps),
            evidence_count=len(source_evidence),
            claim_count=len(source_claims),
            artifact_count=len(source_artifacts),
            event_count=len(source_events),
        )
        await self.events.append(
            session,
            EventDraft(
                name="run.replay.completed",
                aggregate_type="run",
                aggregate_id=target.id,
                organization_id=organization_id,
                correlation_id=target.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=target.id,
                payload={
                    "source_run_id": str(source.id),
                    "source_status": source.status,
                    "snapshot_sha256": snapshot_sha256,
                    "steps": result.step_count,
                    "evidence": result.evidence_count,
                    "claims": result.claim_count,
                    "artifacts": result.artifact_count,
                    "events": result.event_count,
                },
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=organization_id,
                correlation_id=target.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                action="run.replay.materialize",
                resource_type="run",
                resource_id=str(target.id),
                outcome="SUCCESS",
                metadata={
                    "source_run_id": str(source.id),
                    "source_status": source.status,
                    "snapshot_sha256": snapshot_sha256,
                },
            ),
        )
        return result

    @staticmethod
    def _remap(value: Any, replacements: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {key: RunReplayService._remap(item, replacements) for key, item in value.items()}
        if isinstance(value, list):
            return [RunReplayService._remap(item, replacements) for item in value]
        if isinstance(value, str):
            return replacements.get(value, value)
        return copy.deepcopy(value)

    @staticmethod
    def _capability_version_for_step(
        step: RunStep,
        evidence: list[Evidence],
    ) -> UUID | None:
        linked = next((item for item in evidence if item.step_id == step.id), None)
        if linked is None:
            return None
        value = linked.lineage.get("capability_version_id")
        try:
            return UUID(str(value)) if value is not None else None
        except ValueError:
            return None

    @staticmethod
    def _snapshot_fingerprint(
        source: Run,
        steps: list[RunStep],
        evidence: list[Evidence],
        claims: list[Claim],
        links: list[tuple[UUID, UUID]],
        artifacts: list[Artifact],
        events: list[Event],
    ) -> str:
        evidence_fingerprints = {str(item.id): item.content_fingerprint for item in evidence}
        claim_links: dict[str, list[str]] = {}
        for claim_id, evidence_id in links:
            fingerprint = evidence_fingerprints.get(str(evidence_id))
            if fingerprint is not None:
                claim_links.setdefault(str(claim_id), []).append(fingerprint)
        payload = {
            "source_status": source.status,
            "agent_version_id": source.agent_version_id,
            "model_profile_id": source.model_profile_id,
            "intent": source.intent,
            "plan": source.plan,
            "budgets": {
                "max_steps": source.max_steps,
                "timeout_seconds": source.timeout_seconds,
                "max_input_tokens": source.max_input_tokens,
                "max_output_tokens": source.max_output_tokens,
                "max_cost_amount": source.max_cost_amount,
            },
            "usage": {
                "step_count": source.step_count,
                "input_tokens": source.input_tokens,
                "output_tokens": source.output_tokens,
                "cost_amount": source.cost_amount,
            },
            "steps": [
                {
                    "ordinal": item.ordinal,
                    "name": item.name,
                    "kind": item.kind,
                    "status": item.status,
                    "depends_on": item.depends_on,
                    "capability_version_id": item.capability_version_id,
                    "input_payload": item.input_payload,
                    "output_ref": item.output_ref,
                    "retry_count": item.retry_count,
                    "error_code": item.error_code,
                }
                for item in steps
            ],
            "evidence": [
                {
                    "type": item.evidence_type,
                    "source": item.source,
                    "resource": item.resource,
                    "observed_at": item.observed_at,
                    "content": item.content,
                    "content_fingerprint": item.content_fingerprint,
                    "confidence": item.confidence,
                    "classification": item.classification,
                    "permissions": item.permissions,
                    "lineage": item.lineage,
                }
                for item in evidence
            ],
            "claims": [
                {
                    "ordinal": item.ordinal,
                    "statement": item.statement,
                    "confidence": item.confidence,
                    "verification_status": item.verification_status,
                    "critic_notes": item.critic_notes,
                    "evidence_fingerprints": sorted(claim_links.get(str(item.id), [])),
                }
                for item in claims
            ],
            "artifacts": [
                {
                    "kind": item.kind,
                    "title": item.title,
                    "media_type": item.media_type,
                    "inline_content": item.inline_content,
                    "storage_key": item.storage_key,
                    "checksum_sha256": item.checksum_sha256,
                    "classification": item.classification,
                    "acl": item.acl,
                    "lineage": item.lineage,
                }
                for item in artifacts
            ],
            "events": [
                {
                    "sequence": item.sequence,
                    "name": item.name,
                    "schema_version": item.schema_version,
                    "classification": item.classification,
                    "payload": item.payload,
                }
                for item in events
            ],
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()
