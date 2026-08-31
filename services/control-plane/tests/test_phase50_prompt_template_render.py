from __future__ import annotations

import ast
from pathlib import Path

import pytest

from obsion.common.errors import ValidationError
from obsion.registry.prompt_pins import DEFAULT_SYSTEM_POLICY_SCHEMA, DEFAULT_SYSTEM_POLICY_TEMPLATE
from obsion.registry.prompt_render import (
    declared_prompt_variables,
    governed_prompt_values,
    render_prompt_template,
)

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"


def test_system_policy_template_renders_without_placeholders() -> None:
    assert (
        render_prompt_template(DEFAULT_SYSTEM_POLICY_TEMPLATE, DEFAULT_SYSTEM_POLICY_SCHEMA, {})
        == DEFAULT_SYSTEM_POLICY_TEMPLATE
    )
    assert declared_prompt_variables(DEFAULT_SYSTEM_POLICY_SCHEMA) == frozenset()
    assert governed_prompt_values({"route": "KNOWLEDGE"}) == {"route": "KNOWLEDGE"}
    assert governed_prompt_values({}) == {}


def test_prompt_render_is_schema_bound_and_rejects_user_or_secret_variables() -> None:
    schema = {"type": "object", "properties": {"route": {"type": "string"}}}
    rendered = render_prompt_template("Route={route}", schema, {"route": "KNOWLEDGE"})
    assert rendered == "Route=KNOWLEDGE"
    with pytest.raises(ValidationError) as unknown:
        render_prompt_template("Hello {name}", schema, {"route": "KNOWLEDGE"})
    assert unknown.value.code == "prompt_variables_schema_invalid"
    with pytest.raises(ValidationError) as extra:
        render_prompt_template("Route={route}", schema, {"route": "KNOWLEDGE", "extra": "no"})
    assert extra.value.code == "prompt_variables_schema_invalid"
    with pytest.raises(ValidationError) as nested:
        render_prompt_template("Route={route}", schema, {"route": "{nested}"})
    assert nested.value.code == "prompt_variables_schema_invalid"
    with pytest.raises(ValidationError) as secret:
        declared_prompt_variables({"type": "object", "properties": {"api_key": {"type": "string"}}})
    assert secret.value.code == "prompt_secret_denied"
    with pytest.raises(ValidationError) as user:
        declared_prompt_variables(
            {"type": "object", "properties": {"question": {"type": "string"}}}
        )
    assert user.value.code == "prompt_secret_denied"
    with pytest.raises(ValidationError) as leaked:
        render_prompt_template("Route={route}", schema, {"route": "password=super-secret"})
    assert leaked.value.code == "prompt_secret_denied"


def test_harness_renders_pinned_templates_not_user_input() -> None:
    source = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.registry.prompt_render" in imports
    assert "render_prompt_template" in source
    assert "turn.sanitized_input" in source
    assert "render_prompt_template(turn.sanitized_input" not in source
    renderer = (_SOURCE_ROOT / "registry" / "prompt_render.py").read_text(encoding="utf-8")
    assert "eval(" not in renderer
    assert "str.format" not in renderer
    assert "format_map" not in renderer
    assert "Template(" not in renderer
