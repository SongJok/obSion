from collections import Counter
from dataclasses import dataclass
from typing import Any

from obsion.db.models import Evidence


@dataclass(frozen=True, slots=True)
class CriticResult:
    verified: bool
    confidence: float
    coverage: float
    missing_evidence: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    checks: dict[str, bool]


class Critic:
    def verify(
        self,
        evidence: list[Evidence],
        *,
        required_types: tuple[str, ...],
        claims: list[dict[str, Any]],
    ) -> CriticResult:
        substantive = [
            item
            for item in evidence
            if not (
                isinstance(item.content.get("hits"), list)
                and not item.content["hits"]
                and item.content.get("count") == 0
            )
        ]
        available = {item.evidence_type.value for item in substantive}
        missing = tuple(item for item in required_types if item not in available)
        coverage = 1.0 if not required_types else 1 - len(missing) / len(required_types)
        evidence_ids = {str(item.id) for item in evidence}
        claim_links_valid = bool(claims) and all(
            bool(claim.get("evidence_ids"))
            and set(claim.get("evidence_ids", [])).issubset(evidence_ids)
            for claim in claims
        )
        fingerprints = Counter(item.content_fingerprint for item in evidence)
        duplicate_count = sum(count - 1 for count in fingerprints.values() if count > 1)
        conflicts: tuple[dict[str, Any], ...] = ()
        source_diversity = len({item.source for item in evidence})
        confidence = min(
            0.98,
            max(
                0.0,
                coverage * 0.65
                + (0.2 if claim_links_valid else 0.0)
                + min(source_diversity, 3) * 0.05
                - min(duplicate_count, 3) * 0.03,
            ),
        )
        verified = not missing and claim_links_valid and not conflicts
        return CriticResult(
            verified=verified,
            confidence=round(confidence, 4),
            coverage=round(coverage, 4),
            missing_evidence=missing,
            conflicts=conflicts,
            checks={
                "required_evidence": not missing,
                "claim_links": claim_links_valid,
                "source_diversity": source_diversity >= min(2, len(required_types)),
                "duplicate_evidence": duplicate_count == 0,
            },
        )
