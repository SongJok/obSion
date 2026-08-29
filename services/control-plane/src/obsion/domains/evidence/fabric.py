"""Canonical Evidence normalization and persistence primitives.

Every producer may choose its own transport payload, but the durable Evidence
contract is intentionally small and identical for documents, data, logs, code,
deployments, and tool observations. Replay is the only exception: it copies an
already-normalized immutable row verbatim so source fingerprints remain stable.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import Evidence
from obsion.domain.enums import Classification, EvidenceType
from obsion.security.redaction import redact


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    organization_id: UUID
    run_id: UUID
    evidence_type: EvidenceType
    source: str
    resource: str
    content: dict[str, Any]
    observed_at: datetime
    confidence: Decimal | float | int | str = Decimal("1")
    classification: Classification = Classification.INTERNAL
    permissions: tuple[str, ...] = ()
    lineage: dict[str, Any] = field(default_factory=dict)
    step_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NormalizedEvidence:
    evidence_type: EvidenceType
    source: str
    resource: str
    observed_at: datetime
    ingested_at: datetime
    content: dict[str, Any]
    content_fingerprint: str
    confidence: Decimal
    classification: Classification
    permissions: list[str]
    lineage: dict[str, Any]


class EvidenceFabric:
    """Normalize producer output before it enters the Evidence table."""

    @staticmethod
    def normalize(
        item: EvidenceInput, *, ingested_at: datetime | None = None
    ) -> NormalizedEvidence:
        source = item.source.strip()
        resource = item.resource.strip()
        if not source or not resource:
            raise ValueError("Evidence source and resource are required")
        redacted_content = redact(item.content)
        if not isinstance(redacted_content, dict):
            raise ValueError("Evidence content must be a JSON object")
        try:
            confidence = Decimal(str(item.confidence))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Evidence confidence must be numeric") from exc
        if not confidence.is_finite() or not Decimal("0") <= confidence <= Decimal("1"):
            raise ValueError("Evidence confidence must be between 0 and 1")
        safe_lineage = redact(item.lineage)
        if not isinstance(safe_lineage, dict):
            raise ValueError("Evidence lineage must be a JSON object")
        safe_permissions = sorted(
            {permission.strip() for permission in item.permissions if permission.strip()}
        )
        serialized = json.dumps(
            redacted_content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return NormalizedEvidence(
            evidence_type=item.evidence_type,
            source=source,
            resource=resource,
            observed_at=item.observed_at,
            ingested_at=ingested_at or utc_now(),
            content=redacted_content,
            content_fingerprint=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            confidence=confidence,
            classification=item.classification,
            permissions=safe_permissions,
            lineage=safe_lineage,
        )

    async def persist(self, session: AsyncSession, item: EvidenceInput) -> Evidence:
        normalized = self.normalize(item)
        evidence = Evidence(
            id=new_id(),
            organization_id=item.organization_id,
            run_id=item.run_id,
            step_id=item.step_id,
            evidence_type=normalized.evidence_type,
            source=normalized.source,
            resource=normalized.resource,
            observed_at=normalized.observed_at,
            ingested_at=normalized.ingested_at,
            content=normalized.content,
            content_fingerprint=normalized.content_fingerprint,
            confidence=normalized.confidence,
            classification=normalized.classification,
            permissions=normalized.permissions,
            lineage=normalized.lineage,
        )
        session.add(evidence)
        await session.flush()
        return evidence
