"""Bounded lexical anchors for mixed Chinese and Latin text, without a model."""

import re

_CJK = re.compile(r"[\u3400-\u9fff]")
_TERMS = re.compile(r"[\u3400-\u9fff]+|[^\W\u3400-\u9fff]+", re.UNICODE)


def contains_cjk(text: str) -> bool:
    return _CJK.search(text) is not None


def lexical_terms(text: str, *, limit: int = 32) -> list[str]:
    """Keep word tokens and Chinese bigrams; cap unique terms before SQL building."""
    terms: dict[str, None] = {}
    for match in _TERMS.finditer(text.casefold()):
        token = match.group()
        parts = (
            (token[index : index + 2] for index in range(len(token) - 1))
            if contains_cjk(token) and len(token) > 1
            else iter((token,))
        )
        for part in parts:
            terms[part] = None
            if len(terms) >= limit:
                return list(terms)
    return list(terms)
