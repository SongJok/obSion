import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from obsion.db.models import Evidence

_TYPE_ALIASES: dict[str, frozenset[str]] = {
    "DATA": frozenset({"DATA", "SQL"}),
    "SQL": frozenset({"DATA", "SQL"}),
}


@dataclass(frozen=True, slots=True)
class CriticResult:
    verified: bool
    confidence: float
    coverage: float
    missing_evidence: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    checks: dict[str, bool]


class Critic:
    """Deterministic, independent verification of a proposed answer.

    The execution path may be model-backed, but this class never consumes the
    model output as proof.  It only evaluates the immutable Evidence records,
    Claim links and (when supplied) the rendered answer.  Keeping the rules
    deterministic makes a replay produce the same publication decision.
    """

    def verify(
        self,
        evidence: list[Evidence],
        *,
        required_types: tuple[str, ...],
        claims: list[dict[str, Any]],
        claims_required: bool = True,
        route: str | None = None,
        additional_conflicts: tuple[dict[str, Any], ...] = (),
        question: str | None = None,
        answer: str | None = None,
        time_range: dict[str, Any] | None = None,
    ) -> CriticResult:
        substantive = Critic.substantive_records(evidence)
        missing = Critic.missing_required_types(substantive, required_types)
        coverage = 1.0 if not required_types else 1 - len(missing) / len(required_types)
        substantive_ids = {str(item.id) for item in substantive}
        claim_links_valid = (
            not claims_required
            if not claims
            else all(
                isinstance(claim.get("statement"), str)
                and bool(claim.get("statement", "").strip())
                and bool(claim.get("evidence_ids"))
                and set(str(item) for item in claim.get("evidence_ids", [])).issubset(
                    substantive_ids
                )
                for claim in claims
            )
        )
        if route == "INCIDENT" and claims and claim_links_valid:
            evidence_by_id = {str(item.id): item for item in evidence}
            cross_type_claims = all(
                len(
                    {
                        (
                            str(item.evidence_type.value).upper()
                            if hasattr(item.evidence_type, "value")
                            else str(item.evidence_type).upper()
                        )
                        for evidence_id in claim.get("evidence_ids", [])
                        if (item := evidence_by_id.get(str(evidence_id))) is not None
                    }
                )
                >= 2
                for claim in claims
            )
            claim_links_valid = claim_links_valid and cross_type_claims
        elif route == "INCIDENT" and claims:
            # A malformed link set must not be rescued by the cross-type rule.
            claim_links_valid = False
        fingerprints = Counter(item.content_fingerprint for item in evidence)
        duplicate_count = sum(count - 1 for count in fingerprints.values() if count > 1)
        conflicts_list = list(self._detect_conflicts(evidence))
        conflicts_list.extend(self._detect_time_range_conflicts(evidence, time_range))
        conflicts_list.extend(
            self._detect_alternative_explanation_conflicts(evidence, claims, route)
        )
        if (
            claims_required
            and question
            and answer
            and not self._question_is_covered(question, answer, claims)
        ):
            conflicts_list.append(
                {
                    "kind": "VALUE",
                    "severity": "HIGH",
                    "reason": (
                        "Answer does not contain a deterministic signal that the question "
                        "was addressed"
                    ),
                    "reason_codes": ["question_not_covered"],
                }
            )
        conflicts_list.extend(additional_conflicts)
        conflicts = tuple(conflicts_list[:20])
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

    @staticmethod
    def evidence_kind(item: Evidence) -> str:
        value = item.evidence_type
        return str(value.value).upper() if hasattr(value, "value") else str(value).upper()

    @classmethod
    def available_types(cls, evidence: list[Evidence]) -> set[str]:
        return {cls.evidence_kind(item) for item in evidence if cls._substantive(item)}

    @classmethod
    def missing_required_types(
        cls,
        evidence: list[Evidence],
        required_types: tuple[str, ...],
    ) -> tuple[str, ...]:
        available = cls.available_types(evidence)
        missing: list[str] = []
        for item in required_types:
            required = str(item).upper()
            aliases = _TYPE_ALIASES.get(required, frozenset({required}))
            if not aliases & available:
                missing.append(required)
        return tuple(dict.fromkeys(missing))

    @classmethod
    def substantive_records(cls, evidence: list[Evidence]) -> list[Evidence]:
        return [item for item in evidence if cls._substantive(item)]

    @staticmethod
    def _substantive(item: Evidence) -> bool:
        for key in ("hits", "events", "items", "records"):
            values = item.content.get(key)
            if isinstance(values, list) and not values:
                return False
        return True

    @staticmethod
    def _rows(item: Evidence) -> list[dict[str, Any]]:
        rows = (
            item.content.get("events") or item.content.get("items") or item.content.get("records")
        )
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return [item.content]

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _question_is_covered(
        question: str,
        answer: str,
        claims: list[dict[str, Any]],
    ) -> bool:
        """Require a small lexical anchor without attempting semantic grading.

        CJK questions do not have whitespace-delimited words, so for those we
        only require a non-empty answer and at least one Claim.  English and
        other latin-script questions require one meaningful token in the answer
        or a cited Claim statement.
        """
        if not answer.strip() or not claims:
            return False
        if re.search(r"[\u4e00-\u9fff]", question):
            return True
        tokens = {token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", question)}
        response = " ".join(
            [answer, *(str(claim.get("statement", "")) for claim in claims)]
        ).casefold()
        return bool(tokens and any(token in response for token in tokens))

    @classmethod
    def _detect_alternative_explanation_conflicts(
        cls,
        evidence: list[Evidence],
        claims: list[dict[str, Any]],
        route: str | None,
    ) -> tuple[dict[str, Any], ...]:
        if route != "INCIDENT":
            return ()
        causal_claim = any(
            re.search(
                r"\b(caus(?:e|ed|al)|root cause|because|due to)\b|原因|导致|根因",
                str(claim.get("statement", "")),
                re.IGNORECASE,
            )
            for claim in claims
        )
        if not causal_claim:
            return ()
        kinds = {
            str(item.evidence_type.value).upper()
            if hasattr(item.evidence_type, "value")
            else str(item.evidence_type).upper()
            for item in evidence
        }
        signal = {"METRIC", "LOG", "TRACE"} & kinds
        cause = {"DEPLOYMENT", "CONFIG", "CODE", "GIT"} & kinds
        if signal and cause:
            return ()
        return (
            {
                "kind": "SCOPE",
                "severity": "HIGH",
                "reason": (
                    "Causal incident claims require both an observed signal and an "
                    "independent cause artifact"
                ),
                "reason_codes": ["alternative_explanation_unchecked"],
            },
        )

    @classmethod
    def _detect_time_range_conflicts(
        cls,
        evidence: list[Evidence],
        time_range: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], ...]:
        if not isinstance(time_range, dict):
            return ()
        start = cls._parse_time(time_range.get("start"))
        end = cls._parse_time(time_range.get("end"))
        if start is None or end is None or end < start:
            return ()
        conflicts: list[dict[str, Any]] = []
        # A capability is executed after planning, so "now" evidence can land
        # a few milliseconds after the query's computed end.  Keep a bounded
        # acquisition skew while still rejecting genuinely out-of-window data.
        skew = timedelta(minutes=5)
        for item in evidence:
            observed = cls._parse_time(item.observed_at)
            if observed is not None and not (start - skew <= observed <= end + skew):
                conflicts.append(
                    {
                        "evidence_id": str(item.id),
                        "kind": "TEMPORAL",
                        "severity": "HIGH",
                        "reason": "Evidence observation falls outside the requested time range",
                        "reason_codes": ["evidence_outside_time_range"],
                    }
                )
        return tuple(conflicts)

    @staticmethod
    def _detect_conflicts(evidence: list[Evidence]) -> tuple[dict[str, Any], ...]:
        """Detect explicit provider conflicts without guessing across domains.

        Providers may emit a normalized ``conflicts`` array.  We preserve those
        facts for the independent verification result, while the small status
        check catches contradictory metric states from the same service.
        """
        conflicts: list[dict[str, Any]] = []
        for item in evidence:
            declared = item.content.get("conflicts")
            if isinstance(declared, list):
                for conflict in declared[:20]:
                    if isinstance(conflict, dict):
                        conflicts.append(
                            {
                                "evidence_id": str(item.id),
                                "kind": str(conflict.get("kind", "VALUE")),
                                "severity": str(conflict.get("severity", "MEDIUM")),
                                "reason": str(
                                    conflict.get("reason", "Provider reported a conflict")
                                ),
                            }
                        )
        metric_rows: list[tuple[Evidence, dict[str, Any]]] = []
        definitions: dict[tuple[str, str], tuple[Evidence, tuple[str, str, str]]] = {}
        for item in evidence:
            kind = (
                str(item.evidence_type.value).upper()
                if hasattr(item.evidence_type, "value")
                else str(item.evidence_type).upper()
            )
            if kind not in {"METRIC", "DATA", "SQL"}:
                continue
            rows = Critic._rows(item)
            metric_rows.extend((item, row) for row in rows)
            request_resource = item.lineage.get("request_resource", {})
            if not isinstance(request_resource, dict):
                request_resource = {}
            resource_metric = request_resource.get("metric", {})
            if not isinstance(resource_metric, dict):
                resource_metric = {}
            for row in rows:
                validation = (
                    row.get("validation")
                    or item.content.get("validation")
                    or request_resource.get("validation")
                )
                if isinstance(validation, dict):
                    if validation.get("valid") is False or validation.get("read_only") is False:
                        conflicts.append(
                            {
                                "evidence_id": str(item.id),
                                "kind": "VALUE",
                                "severity": "HIGH",
                                "reason": (
                                    "SQL evidence failed gateway validation or read-only "
                                    "enforcement"
                                ),
                                "reason_codes": ["sql_reliability_failed"],
                            }
                        )
                    if validation.get("error"):
                        conflicts.append(
                            {
                                "evidence_id": str(item.id),
                                "kind": "VALUE",
                                "severity": "HIGH",
                                "reason": "SQL evidence contains a validation error",
                                "reason_codes": ["sql_reliability_failed"],
                            }
                        )
                metric_name = str(
                    row.get("metric")
                    or row.get("metric_name")
                    or row.get("measure")
                    or item.content.get("metric")
                    or item.content.get("metric_name")
                    or resource_metric.get("name")
                    or resource_metric.get("display_name")
                    or ""
                ).strip()
                if not metric_name:
                    continue
                subject = str(row.get("subject") or row.get("service") or "").strip()
                definition = str(
                    row.get("definition_version")
                    or row.get("definitionVersion")
                    or item.lineage.get("definition_version")
                    or item.content.get("definition_version")
                    or resource_metric.get("version")
                    or ""
                ).strip()
                signature = (
                    str(row.get("unit") or item.content.get("unit") or "").strip(),
                    str(
                        row.get("environment")
                        or item.lineage.get("environment")
                        or request_resource.get("environment")
                        or ""
                    ).strip(),
                    definition,
                )
                key = (subject, metric_name)
                previous = definitions.get(key)
                if previous is not None and previous[1] != signature:
                    conflicts.append(
                        {
                            "left_evidence_id": str(previous[0].id),
                            "right_evidence_id": str(item.id),
                            "kind": "DEFINITION",
                            "severity": "HIGH",
                            "reason": (
                                "Metric evidence uses incompatible unit, environment, or "
                                "definition version"
                            ),
                            "reason_codes": ["metric_definition_mismatch"],
                        }
                    )
                else:
                    definitions[key] = (item, signature)

        # All evidence types may carry explicit validity intervals.  Checking
        # them here catches malformed provider records before an answer is
        # published, without imposing a fixed window on otherwise valid data.
        for item in evidence:
            for row in Critic._rows(item):
                valid_from = Critic._parse_time(
                    row.get("valid_from") or row.get("start_time") or row.get("timestamp")
                )
                valid_to = Critic._parse_time(row.get("valid_to") or row.get("end_time"))
                if valid_from is not None and valid_to is not None and valid_to < valid_from:
                    conflicts.append(
                        {
                            "evidence_id": str(item.id),
                            "kind": "TEMPORAL",
                            "severity": "HIGH",
                            "reason": "Evidence validity interval ends before it starts",
                            "reason_codes": ["temporal_inconsistency"],
                        }
                    )
        for index, (left_item, left) in enumerate(metric_rows):
            left_service = str(left.get("service") or left.get("service_name") or "")
            left_status = str(left.get("status") or left.get("state") or "").casefold()
            if not left_status:
                continue
            for right_item, right in metric_rows[index + 1 :]:
                if left_item.id == right_item.id:
                    continue
                right_service = str(right.get("service") or right.get("service_name") or "")
                right_status = str(right.get("status") or right.get("state") or "").casefold()
                if left_service and right_service and left_service != right_service:
                    continue
                if right_status and right_status != left_status:
                    conflicts.append(
                        {
                            "left_evidence_id": str(left_item.id),
                            "right_evidence_id": str(right_item.id),
                            "kind": "VALUE",
                            "severity": "MEDIUM",
                            "reason": "Metric signals for the same service report different states",
                        }
                    )
        return tuple(conflicts[:20])
