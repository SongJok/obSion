from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from obsion.common.errors import ValidationError
from obsion.security.redaction import redact_text

_PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_FORBIDDEN_VARIABLES = frozenset(
    {
        "accesskey",
        "apikey",
        "credential",
        "input",
        "message",
        "password",
        "prompt",
        "question",
        "secret",
        "token",
        "user",
        "userinput",
    }
)


def declared_prompt_variables(schema: Mapping[str, Any] | None) -> frozenset[str]:
    document = dict(schema or {})
    schema_type = document.get("type", "object")
    if schema_type != "object" or not isinstance(document.get("properties", {}), dict):
        raise ValidationError(
            "prompt_variables_schema_invalid",
            "Prompt variables must be an object JSON Schema",
        )
    properties = document.get("properties") or {}
    names = frozenset(str(name) for name in properties)
    blocked = sorted(
        name for name in names if re.sub(r"[^a-z0-9]", "", name.casefold()) in _FORBIDDEN_VARIABLES
    )
    if blocked:
        raise ValidationError(
            "prompt_secret_denied",
            "Prompt variables cannot interpolate secrets or untrusted user text",
            names=blocked,
        )
    return names


def render_prompt_template(
    template: str,
    schema: Mapping[str, Any] | None,
    values: Mapping[str, Any] | None = None,
) -> str:
    declared = declared_prompt_variables(schema)
    used = frozenset(_PLACEHOLDER.findall(template))
    provided = {str(key): value for key, value in dict(values or {}).items()}
    extra = sorted(set(provided) - declared)
    unknown = sorted(used - declared)
    missing = sorted(used - set(provided))
    if extra or unknown or missing:
        raise ValidationError(
            "prompt_variables_schema_invalid",
            "Prompt template variables must match the published schema and provided values",
            extra=extra,
            unknown=unknown,
            missing=missing,
        )

    def _replace(match: re.Match[str]) -> str:
        raw = provided[match.group(1)]
        if not isinstance(raw, str) or not raw or redact_text(raw) != raw:
            raise ValidationError(
                "prompt_secret_denied",
                "Prompt variable values must be non-empty redacted strings",
            )
        if "{" in raw or "}" in raw:
            raise ValidationError(
                "prompt_variables_schema_invalid",
                "Prompt variable values cannot contain further placeholders",
            )
        return raw

    return _PLACEHOLDER.sub(_replace, template)


def governed_prompt_values(plan: Mapping[str, Any] | None) -> dict[str, str]:
    route = dict(plan or {}).get("route")
    if isinstance(route, str) and route.strip():
        return {"route": route.strip()}
    return {}
