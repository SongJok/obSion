from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from obsion.model_gateway.context import ContextSegment, TrustLevel
from obsion.security.redaction import redact_text


def snapshot_workspace(
    *,
    workspace_id: UUID,
    name: str,
    classification: str,
    visibility: str,
    description: str,
) -> dict[str, Any]:
    safe_name = redact_text(name)
    safe_description = redact_text(description or "")
    return {
        "workspace_id": str(workspace_id),
        "name": safe_name,
        "classification": classification,
        "visibility": visibility,
        "description": safe_description,
        "description_fingerprint": hashlib.sha256(safe_description.encode()).hexdigest(),
    }


def workspace_context_segments(pin: dict[str, Any] | None) -> list[ContextSegment]:
    if not pin or not pin.get("workspace_id"):
        return []
    identity = json.dumps(
        {
            "workspace_id": pin.get("workspace_id"),
            "name": pin.get("name"),
            "classification": pin.get("classification"),
            "visibility": pin.get("visibility"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    segments = [
        ContextSegment(TrustLevel.AGENT, identity, "workspace-identity", 870, 260),
    ]
    description = str(pin.get("description") or "")
    if description.strip():
        segments.append(
            ContextSegment(
                TrustLevel.UNTRUSTED_DATA,
                description,
                "workspace-description",
                650,
                270,
            )
        )
    return segments
