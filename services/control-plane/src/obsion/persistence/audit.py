from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import AuditRecord
from obsion.domain.enums import ActorType, Classification, RiskLevel
from obsion.security.redaction import redact


@dataclass(frozen=True, slots=True)
class AuditDraft:
    organization_id: UUID
    correlation_id: UUID
    actor_type: ActorType
    actor_id: UUID | None
    action: str
    resource_type: str
    outcome: str
    resource_id: str | None = None
    risk_level: RiskLevel | None = None
    policy_decision_id: UUID | None = None
    approval_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    agent_version_id: UUID | None = None
    model_profile_id: UUID | None = None
    capability_version_id: UUID | None = None
    resource: dict[str, Any] | None = None
    result_classification: Classification | None = None


class AuditWriter:
    async def write(self, session: AsyncSession, draft: AuditDraft) -> AuditRecord:
        metadata = dict(draft.metadata)
        canonical_dimensions: dict[str, Any] = {
            "agent_version_id": draft.agent_version_id,
            "model_profile_id": draft.model_profile_id,
            "capability_version_id": draft.capability_version_id,
            "resource": draft.resource,
            "policy_decision_id": draft.policy_decision_id,
            "approval_id": draft.approval_id,
            "risk_level": draft.risk_level,
            "result_classification": draft.result_classification,
            "latency_ms": draft.latency_ms,
        }
        for key, value in canonical_dimensions.items():
            if value is not None:
                metadata.setdefault(key, value)
        record = AuditRecord(
            id=new_id(),
            organization_id=draft.organization_id,
            correlation_id=draft.correlation_id,
            actor_type=draft.actor_type,
            actor_id=draft.actor_id,
            action=draft.action,
            resource_type=draft.resource_type,
            resource_id=draft.resource_id,
            outcome=draft.outcome,
            risk_level=draft.risk_level,
            policy_decision_id=draft.policy_decision_id,
            approval_id=draft.approval_id,
            redacted_metadata=redact(jsonable_encoder(metadata)),
            latency_ms=draft.latency_ms,
            created_at=utc_now(),
        )
        session.add(record)
        await session.flush()
        return record
