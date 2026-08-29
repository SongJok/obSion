from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from obsion.db.models import Evidence
from obsion.domain.enums import Classification, EvidenceType
from obsion.harness.critic import Critic


def evidence(kind: EvidenceType, source: str, fingerprint: str) -> Evidence:
    now = datetime.now(UTC)
    return Evidence(
        id=uuid4(),
        organization_id=uuid4(),
        run_id=uuid4(),
        evidence_type=kind,
        source=source,
        resource=f"resource://{source}",
        observed_at=now,
        ingested_at=now,
        content={"value": 1},
        content_fingerprint=fingerprint,
        confidence=Decimal("1.0"),
        classification=Classification.INTERNAL,
        permissions=[],
        lineage={},
    )


def test_critic_verifies_covered_claims() -> None:
    items = [evidence(EvidenceType.LOG, "logs", "a"), evidence(EvidenceType.TRACE, "traces", "b")]
    result = Critic().verify(
        items,
        required_types=("LOG", "TRACE"),
        claims=[
            {
                "statement": "Latency followed the release",
                "evidence_ids": [str(item.id) for item in items],
            }
        ],
    )
    assert result.verified
    assert result.coverage == 1.0
    assert result.confidence >= 0.9


def test_critic_rejects_missing_or_unlinked_evidence() -> None:
    item = evidence(EvidenceType.LOG, "logs", "a")
    result = Critic().verify(
        [item],
        required_types=("LOG", "DEPLOYMENT"),
        claims=[{"statement": "A deployment caused the incident", "evidence_ids": []}],
    )
    assert not result.verified
    assert result.missing_evidence == ("DEPLOYMENT",)
    assert not result.checks["claim_links"]


def test_critic_never_verifies_an_empty_retrieval_or_empty_claim_set() -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "empty")
    item.content = {"hits": [], "count": 0}
    result = Critic().verify([item], required_types=("DOCUMENT",), claims=[])

    assert not result.verified
    assert result.missing_evidence == ("DOCUMENT",)
    assert not result.checks["claim_links"]


def test_critic_can_verify_non_factual_responses_without_claims() -> None:
    result = Critic().verify([], required_types=(), claims=[], claims_required=False)

    assert result.verified
    assert result.coverage == 1.0
    assert result.checks["claim_links"]
