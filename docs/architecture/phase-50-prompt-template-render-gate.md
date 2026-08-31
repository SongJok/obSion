# Phase 50 Prompt template render review

## Review question

Are pinned PromptVersion templates rendered with schema-bound governed values only,
without interpolating user text, secrets, or nested placeholders into SYSTEM trust?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `{name}` substitution requires a matching object-schema property and provided value.
- Secret-like and user-like variable names are `prompt_secret_denied`.
- Extra, missing, unknown, or nested placeholder values are
  `prompt_variables_schema_invalid`.
- Harness passes only governed `route` from the pinned Run plan. User input is a
  separate USER segment.
- `obsion-system-policy` v1 has no placeholders and renders unchanged.
- No eval, str.format, or Jinja.

## Automated acceptance map

- `test_phase50_prompt_template_render.py` covers identity render, schema binding,
  secret/user rejection, and AST bans.

## Human review checklist

- Confirm operators do not publish Prompt variables that restate the user turn.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
