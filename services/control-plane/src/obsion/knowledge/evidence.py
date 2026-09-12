"""One stable body ordering for document authoring, review and source display."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from obsion.domain.enums import EvidenceType


@dataclass(frozen=True, slots=True)
class DocumentBody:
    body_index: int
    text: str
    metadata: Mapping[str, Any]


def document_bodies(item: Any) -> list[DocumentBody]:
    kind = getattr(item, "evidence_type", None)
    if getattr(kind, "value", kind) != EvidenceType.DOCUMENT:
        return []
    content = item.content
    candidates = [(content.get(key), content) for key in ("text", "content")]
    hits = content.get("hits")
    if isinstance(hits, list):
        candidates.extend((hit.get("content"), hit) for hit in hits if isinstance(hit, dict))
    bodies: list[DocumentBody] = []
    for text, metadata in candidates:
        if isinstance(text, str) and text.strip():
            bodies.append(DocumentBody(len(bodies), text, metadata))
    return bodies
