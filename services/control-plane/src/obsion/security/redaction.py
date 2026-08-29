import re
from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_CREDENTIAL_URI = re.compile(r"(?P<scheme>\w+://)(?P<user>[^:/\s]+):(?P<password>[^@/\s]+)@")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<key>password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
)


def redact_text(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}[REDACTED]",
        value,
    )
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _CREDENTIAL_URI.sub(r"\g<scheme>[REDACTED]@", value)


def redact(value: Any, *, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value
