# PHASE-49-REPORT — Runtime Prompt pin

## What was implemented

Phase 49 pins PromptVersion onto the Harness Run so Prompt Change can be evaluated
without rewriting production templates.

- `runs.prompt_pins` is set at Turn creation: `obsion-system-policy` plus optional
  AgentSpec `prompts`. Replay copies the pin. Alembic `a49f6c1d8e20`.
- Builtin registry seeds ACTIVE `obsion-system-policy` v1 with the former hardcoded
  Context Builder SYSTEM policy. Harness loads the pinned template; checksum
  mismatch is `prompt_pin_mismatch`.
- Eval start accepts `prompt_pins`. Catalog lists Prompt versions. Compare returns
  `prompt_changed`. Workbench 评测台 exposes the pin select.
- Studio still refuses Prompt rollback. No Prompt `active_version`. No traffic split.

## Architecture decisions

Latest-at-synthesize would make historical Runs non-reproducible. Pin at Turn
creation matches AgentVersion. Prompt cutover stays "publish a new snapshot"; new
Turns pick it up, old Turns do not. ADR 0028 records that boundary.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 611 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase49_runtime_prompt_pin.py`.
- Architecture AST: Harness imports `load_pinned_templates` and no longer embeds
  the SYSTEM policy string. Studio still has no `PromptDefinition.active_version`.
- OpenAPI regenerated. Error catalog includes `prompt_pin_mismatch`.
- Composer still has no Agent picker.

## Remaining risks

- Pinned templates are used as raw SYSTEM/AGENT text. `variables_schema`
  interpolation is not implemented.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  operator-owned or blocked on real tenant artifacts.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
