"""Stable acceptance failure taxonomy; never infer facts from model prose."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Literal

FailureCategory = Literal[
    "SOURCE",
    "SEMANTIC",
    "CITATION",
    "MODEL_PROTOCOL",
    "ENDPOINT",
    "RESOURCE",
    "FRESHNESS",
    "UNCLASSIFIED",
]

_QUOTE_DIAGNOSTICS = frozenset(
    {"answer_quote_invalid", "source_quote_invalid", "quote_not_substantive"}
)
_PROTOCOL_DIAGNOSTICS = frozenset(
    {
        "json_invalid",
        "json_duplicate_key",
        "json_depth_invalid",
        "schema_invalid",
        "rule_coverage_invalid",
        "rule_support_missing",
        "protocol_invalid",
    }
)


def _judgment_diagnostic(result: Mapping[str, Any]) -> str | None:
    score = result.get("score")
    if not isinstance(score, Mapping):
        return None
    evidence = score.get("evidence")
    if not isinstance(evidence, list):
        return None
    diagnostics = {
        item.get("judgment_diagnostic")
        for item in evidence
        if isinstance(item, Mapping) and isinstance(item.get("judgment_diagnostic"), str)
    }
    if len(diagnostics) != 1:
        return None
    return diagnostics.pop()


def classify_acceptance_result(result: Mapping[str, Any]) -> FailureCategory | None:
    """Classify only explicit machine reasons and diagnostics.

    Unknown reasons remain UNCLASSIFIED so a reporting layer cannot turn a guess
    into a root-cause claim.  PASS has no failure category.
    """

    if result.get("status") == "PASS":
        return None
    diagnostic = _judgment_diagnostic(result)
    if diagnostic in _QUOTE_DIAGNOSTICS:
        return "CITATION"
    if diagnostic in _PROTOCOL_DIAGNOSTICS:
        return "MODEL_PROTOCOL"

    reason = result.get("reason")
    if not isinstance(reason, str) or not reason:
        return "UNCLASSIFIED"
    normalized = reason.casefold()
    if any(token in normalized for token in ("fresh", "stale", "generation_missing")):
        return "FRESHNESS"
    if any(token in normalized for token in ("citation", "quote")):
        return "CITATION"
    if reason == "independent_judgment_invalid" or any(
        token in normalized for token in ("protocol", "schema_invalid", "output_truncated")
    ):
        return "MODEL_PROTOCOL"
    if any(
        token in normalized
        for token in (
            "model_unavailable",
            "model_timeout",
            "endpoint",
            "scorer_not_configured",
            "scorer_failed",
        )
    ):
        return "ENDPOINT"
    if any(
        token in normalized
        for token in ("source", "document", "published_source", "reviewed_source")
    ):
        return "SOURCE"
    if any(token in normalized for token in ("semantic", "answer_failed", "grounding")):
        return "SEMANTIC"
    if any(
        token in normalized
        for token in (
            "timeout",
            "budget",
            "rate_limit",
            "resource",
            "task_did_not_complete",
            "task_observation_failed",
        )
    ):
        return "RESOURCE"
    return "UNCLASSIFIED"


def summarize_failure_categories(results: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(
        category
        for result in results
        if (category := classify_acceptance_result(result)) is not None
    )
    return dict(sorted(counts.items()))


def _evaluation_policy_sha256() -> str:
    # Imported lazily to avoid the acceptance <-> semantic model import cycle.
    from obsion.evaluations.semantic import POLICY_SHA256

    return POLICY_SHA256
