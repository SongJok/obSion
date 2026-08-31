# PHASE-48-REPORT — Agent / Prompt versioning

## What was implemented

Phase 48 delivers goal.txt Prompt / Agent version management: Agent v1/v2 can be
compared, rolled back, and evaluated. Production Prompt text is not rewritten.

- `POST /api/v1/studio/compare` returns a redacted mapping diff of two Agent, Skill,
  or Prompt snapshots. `traffic_split` is always `false`. The evaluation hint tells
  operators to pin each version on separate Evaluation Runs of the same Golden
  Dataset snapshot. `fixtures.actual` is rejected.
- `POST /api/v1/studio/rollback` restores an Agent or Skill `active_version` by
  promoting a previously published checksummed version. Both versions remain. Prompt
  rollback is `registry_spec_invalid`.
- Workbench Studio lists every version, offers 回滚到此版本 and 对比版本, and shows
  `no traffic split`. Python and TypeScript SDKs wrap the routes.
- No schema migration. PromptDefinition still has no `active_version`. Harness still
  binds the promoted Agent/Skill version at Turn creation.

## Architecture decisions

Runtime percentage A/B would be a second router. Compare is a registry diff; Evaluate
reuses the existing Eval console. Prompt cutover without a Run pin would fake a
production switch. ADR 0027 records that boundary.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 611 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase48_agent_prompt_versioning.py`.
- Architecture AST: Studio does not import Harness or Gateway; `traffic_split` is
  hardcoded false; `PromptDefinition.active_version` is absent.
- OpenAPI regenerated. Error origin sinks for compare/rollback/load are reviewed.
- Composer still has no Agent picker.

## Remaining risks

- Runtime Prompt pin on Turn/Run is not implemented. Eval still resolves latest
  Prompt versions unless a later phase pins them.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  operator-owned or blocked on real tenant artifacts.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
