from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from obsion.domain.enums import EvidenceType
from obsion.model_gateway.context import ContextSegment, TrustLevel


def evidence_context_segments(evidence: Sequence[Any]) -> list[ContextSegment]:
    tools: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for item in evidence:
        payload = _payload(item)
        if _evidence_type(item) == EvidenceType.TOOL:
            tools.append(payload)
        else:
            observed.append(payload)
    segments = [
        ContextSegment(
            TrustLevel.UNTRUSTED_DATA,
            json.dumps(observed, ensure_ascii=False, default=str),
            "evidence-bus",
            800,
            800,
        )
    ]
    if tools:
        segments.append(
            ContextSegment(
                TrustLevel.UNTRUSTED_DATA,
                json.dumps(tools, ensure_ascii=False, default=str),
                "tool-result",
                790,
                810,
            )
        )
    return segments


def _evidence_type(item: Any) -> str:
    value = getattr(item, "evidence_type", None)
    return str(getattr(value, "value", value) or "")


def _payload(item: Any) -> dict[str, Any]:
    observed_at = getattr(item, "observed_at", None)
    return {
        "id": str(item.id),
        "type": _evidence_type(item),
        "source": item.source,
        "resource": item.resource,
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "content": item.content,
    }
