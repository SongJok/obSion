"""Server-owned, exact source passages for model citation selection."""

import hashlib

from obsion.security.redaction import redact_text

PASSAGE_CHARACTERS = 1500


def source_passages(
    sources: dict[str, list[str]],
) -> tuple[dict[str, list[dict[str, str]]], dict[tuple[str, str], str]]:
    """Partition current bodies; never offer a redacted passage as exact evidence.

    Identifiers are bound to this Evidence, body, offset and text. The caller keeps
    the lookup locally; model output cannot supply replacement source text.
    """
    displayed: dict[str, list[dict[str, str]]] = {}
    lookup: dict[tuple[str, str], str] = {}
    for evidence_id, bodies in sources.items():
        displayed[evidence_id] = []
        for body_index, body in enumerate(bodies):
            # Redact before partitioning: a credential/private-key block must not
            # become invisible to the redactor by crossing a passage boundary.
            if redact_text(body) != body:
                continue
            start = 0
            while start < len(body):
                end = min(len(body), start + PASSAGE_CHARACTERS)
                if end < len(body):
                    boundary = body.rfind("\n", start + PASSAGE_CHARACTERS // 2, end)
                    if boundary >= 0:
                        end = boundary + 1
                text = body[start:end]
                digest = hashlib.sha256(
                    f"{evidence_id}:{body_index}:{start}:".encode() + text.encode()
                ).hexdigest()
                passage_id = f"p-{digest}"
                start = end
                if not text.strip():
                    continue
                displayed[evidence_id].append({"passage_id": passage_id, "text": text})
                lookup[(evidence_id, passage_id)] = text
    return displayed, lookup
