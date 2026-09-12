"""Detect transport references without suppressing literal document content."""

import re
from collections.abc import Sequence
from typing import Any

from obsion.knowledge.evidence import document_bodies

_FIELD_REFERENCE = re.compile(
    r"\b(?:body_index|chunk_id|evidence_id|document_id)\b\s*(?:[:=]\s*)?"
    r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)\b",
    re.IGNORECASE,
)


def contains_transport_references(answer: str, evidence: Sequence[Any]) -> bool:
    bodies = [body.text for item in evidence for body in document_bodies(item)]
    references = [str(item.id) for item in evidence if str(item.id) in answer]
    references.extend(match.group() for match in _FIELD_REFERENCE.finditer(answer))
    return any(not any(reference in body for body in bodies) for reference in references)
