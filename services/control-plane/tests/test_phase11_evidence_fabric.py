from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from obsion.domain.enums import Classification, EvidenceType
from obsion.domains.evidence.fabric import EvidenceFabric, EvidenceInput


def test_evidence_fabric_normalizes_redaction_and_fingerprint() -> None:
    item = EvidenceInput(
        organization_id=uuid4(),
        run_id=uuid4(),
        evidence_type=EvidenceType.LOG,
        source="  observability  ",
        resource="  service://checkout  ",
        observed_at=datetime(2026, 8, 29, tzinfo=UTC),
        content={"message": "failed", "token": "never-persist"},
        confidence="0.875",
        classification=Classification.CONFIDENTIAL,
        permissions=("logs.read", "logs.read", " "),
        lineage={"request_id": "r-1", "api_key": "never-persist"},
    )

    normalized = EvidenceFabric.normalize(item)

    assert normalized.source == "observability"
    assert normalized.resource == "service://checkout"
    assert normalized.content == {"message": "failed", "token": "[REDACTED]"}
    assert normalized.lineage == {"request_id": "r-1", "api_key": "[REDACTED]"}
    assert normalized.permissions == ["logs.read"]
    assert normalized.confidence == Decimal("0.875")
    assert len(normalized.content_fingerprint) == 64
    assert normalized.content_fingerprint == EvidenceFabric.normalize(item).content_fingerprint


@pytest.mark.parametrize("confidence", ["not-a-number", -0.1, 1.1, "NaN"])
def test_evidence_fabric_rejects_invalid_confidence(confidence: object) -> None:
    item = EvidenceInput(
        organization_id=uuid4(),
        run_id=uuid4(),
        evidence_type=EvidenceType.DOCUMENT,
        source="knowledge",
        resource="document://one",
        observed_at=datetime(2026, 8, 29, tzinfo=UTC),
        content={"text": "safe"},
        confidence=confidence,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="confidence"):
        EvidenceFabric.normalize(item)
