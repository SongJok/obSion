"""A bounded document-grounding check, not a semantic entailment verifier.

Recognize explicit Arabic-digit money and percentages, preserving currency,
sign and scale. Never treat a title, URL, score or unrelated Evidence as proof.
Unmarked numbers, derived arithmetic and Chinese numeral words are outside
this check. Values occurring in a source still require semantic verification.
"""

import re
import unicodedata
from decimal import Decimal
from typing import Any

from obsion.db.models import Evidence

_NUMBER = r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_BOUNDARY = r"(?<![A-Za-z0-9_.,+\-])"
_CURRENCY = r"人民币|美元|美金|欧元|RMB|CNY|USD|EUR|US\$|元|¥|\$|€"
_PREFIX = re.compile(
    rf"{_BOUNDARY}(?P<currency>{_CURRENCY})\s*(?P<number>{_NUMBER})"
    r"\s*(?P<scale>万|亿)?",
    re.IGNORECASE,
)
_SUFFIX = re.compile(
    rf"{_BOUNDARY}(?P<number>{_NUMBER})\s*(?P<scale>万|亿)?"
    rf"\s*(?P<currency>{_CURRENCY})(?![A-Za-z])",
    re.IGNORECASE,
)
_PERCENT = re.compile(rf"{_BOUNDARY}(?P<number>{_NUMBER})\s*(?:%|percent\b)", re.IGNORECASE)
_CNY = frozenset({"人民币", "元", "¥", "rmb", "cny"})
_USD = frozenset({"美元", "美金", "$", "us$", "usd"})
Quantity = tuple[str, Decimal]


def quantities(text: str) -> frozenset[Quantity]:
    normalized = unicodedata.normalize("NFKC", text)
    found: set[Quantity] = set()
    for pattern in (_PREFIX, _SUFFIX, _PERCENT):
        for match in pattern.finditer(normalized):
            value = Decimal(match["number"].replace(",", ""))
            if pattern is _PERCENT:
                found.add(("percentage", value))
                continue
            currency = match["currency"].casefold()
            unit = "CNY" if currency in _CNY else "USD" if currency in _USD else "EUR"
            shift = {"万": 4, "亿": 8}.get(match["scale"], 0)
            parts = value.as_tuple()
            # Decimal multiplication uses process precision; shifting the tuple
            # keeps every supplied digit without mutating a global context.
            assert isinstance(parts.exponent, int)
            value = Decimal((parts.sign, parts.digits, parts.exponent + shift))
            found.add((unit, value))
    return frozenset(found)


def _document_body(item: Evidence) -> str:
    kind = getattr(item.evidence_type, "value", item.evidence_type)
    if kind != "DOCUMENT":
        return ""
    content = item.content
    bodies = [content[key] for key in ("text", "content") if isinstance(content.get(key), str)]
    hits = content.get("hits")
    if isinstance(hits, list):
        bodies.extend(
            hit["content"]
            for hit in hits
            if isinstance(hit, dict) and isinstance(hit.get("content"), str)
        )
    return "\n".join(bodies)


def quantity_conflicts(
    evidence: list[Evidence], claims: list[dict[str, Any]], answer: str | None
) -> tuple[dict[str, Any], ...]:
    available = {str(item.id): quantities(_document_body(item)) for item in evidence}
    answer_support: set[Quantity] = set()
    unsupported_claim = False
    for claim in claims:
        links = claim.get("evidence_ids")
        if not isinstance(links, list):
            continue  # The existing Claim link checker rejects malformed links.
        supported: set[Quantity] = set()
        for link in links:
            supported.update(available.get(str(link), ()))
        answer_support.update(supported)
        statement = claim.get("statement")
        if isinstance(statement, str) and not quantities(statement).issubset(supported):
            unsupported_claim = True
    if unsupported_claim or (
        answer is not None and not quantities(answer).issubset(answer_support)
    ):
        return (
            {
                "kind": "VALUE",
                "severity": "HIGH",
                "reason": (
                    "A monetary or percentage value is absent from the linked document bodies"
                ),
                "reason_codes": ["quantity_not_grounded"],
            },
        )
    return ()
