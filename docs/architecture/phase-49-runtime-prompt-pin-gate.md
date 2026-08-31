# Phase 49 runtime Prompt pin review

## Review question

Does each Harness Run pin PromptVersion at Turn creation, does Context Builder use
that pin instead of latest/hardcoded text, and can Eval compare two Prompt versions
without rewriting production templates or splitting traffic?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `runs.prompt_pins` is JSON set at Turn creation from `obsion-system-policy` plus
  AgentSpec `prompts`. Replay copies the pin.
- Context Builder SYSTEM/AGENT prompt segments load by pinned `version_id`.
  Checksum mismatch is `prompt_pin_mismatch`.
- Builtin registry seeds `obsion-system-policy` v1 with the previous platform policy
  text. Alembic `a49f6c1d8e20` adds the column.
- Eval start accepts `prompt_pins: {name: version}`. Catalog lists Prompt versions.
  Compare returns `prompt_changed`. Unknown pin names are `registry_spec_invalid`.
- Studio Prompt rollback remains denied. `PromptDefinition.active_version` is not
  added. `traffic_split` remains false.
- Conversation still has no Agent picker.

## Automated acceptance map

- `test_phase49_runtime_prompt_pin.py` covers Turn pin/replay, Eval v1 vs v2
  compare, unknown pin names, checksum fail-closed, and AST (Harness no longer
  embeds the SYSTEM policy string).
- `test_phase35_experience_eval.py` asserts `prompt_changed` on identical Prompt pins.

## Human review checklist

- Confirm operators pin Prompt versions on Eval before treating Prompt Change as a
  release gate.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
