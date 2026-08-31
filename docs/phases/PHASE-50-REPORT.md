# PHASE-50-REPORT — Prompt template render

## What was implemented

Phase 50 renders pinned PromptVersion templates with a fail-closed, schema-bound
substitutor. Context Builder does not interpolate the user turn into SYSTEM trust.

- `obsion.registry.prompt_render.render_prompt_template` replaces `{name}` only when
  `name` is a `variables_schema` property and the value is a non-empty redacted
  string without nested braces.
- Secret-like and user-like names (`token`, `question`, `input`, …) are
  `prompt_secret_denied`. Extra/missing/unknown placeholders are
  `prompt_variables_schema_invalid`.
- Harness supplies governed `route` from the Run plan. `obsion-system-policy` v1
  has an empty object schema and is unchanged. No schema migration.

## Architecture decisions

General templating (Jinja, format, eval) would be an injection surface. ADR 0029
keeps substitution explicit and schema-bound. User text remains a USER/untrusted
Context Builder segment.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 611 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase50_prompt_template_render.py`.
- Architecture AST: Harness does not pass `turn.sanitized_input` to the renderer;
  the renderer has no eval/format/Jinja.

## Remaining risks

- Only `route` is a governed interpolant today. Additional AgentSpec fields would
  need a later allowlist, not user-turn interpolation.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
