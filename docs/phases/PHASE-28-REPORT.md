# PHASE-28-REPORT — Reflect-driven critic replan

## What was implemented

Phase 28 makes the Phase 27 REFLECT step operational against the Harness contract:
Verify → Reflect → replan or respond.

- Critic and the missing-type scanner share `Critic.substantive_records`. Empty
  `events`/`hits`/`items`/`records` lists no longer satisfy a required type.
- `_apply_gap_replan` is the single insertion helper used before `_respond` and after
  VERIFY. Both `critic_missing_evidence` and `critic_verification_failed` count toward
  `run_max_critic_replans`.
- After Critic, Reflect chooses `RESPOND`, `REPLAN`, or `WITHHOLD`. A successful
  REPLAN reopens VERIFY, does not start RESPOND, and returns the Run to Execute.
- ADR 0007 records that conflict-only failures still withhold rather than inventing
  extra capabilities.

## Architecture decisions

Publication remains Phase 20: unverified answers with no remaining authorized
capability become WITHHOLD/PARTIAL. The execute loop continues after `_respond`
returns true so Web, CLI, and future IDE clients see the same replanning Run.

## Validation

- `uv run pytest --no-cov` — 438 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase28_reflect_replan.py` and updated Reflect decision tests.
- Event helper callers for gap insertion moved to `HarnessRuntime._apply_gap_replan`.
- `uv run ruff check` on the Harness runtime and Critic, mypy on those modules.

## Remaining risks

- Critic failures that are not missing types (conflicts, question coverage) do not
  select extra tools.
- Staging deploy and human security sign-off remain operator-owned.
