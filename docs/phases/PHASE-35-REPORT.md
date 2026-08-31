# PHASE-35-REPORT — Experience Eval

## What was implemented

Phase 35 adds Obsion Eval as a governed evaluation console on the existing control
plane. It does not implement a second Harness and does not present an Agent picker
in conversation.

- `GET /api/v1/eval/catalog`, dataset/case/run REST, and `POST /api/v1/eval/compare`.
- Validation reuses Golden Dataset contracts. `fixtures.actual` is rejected on both
  `/eval` and `/admin/evaluations`.
- Catalog pins promoted Agent versions and enabled model profiles. Compare requires
  two completed runs on the same dataset snapshot.
- Workbench **评测台** creates datasets, adds cases, starts runs, and compares
  baselines. Runtime rollback remains Studio promote.
- ADR 0014 records that Eval is a console over `EvaluationService`, not a runtime.
- No schema migration; evaluation tables from earlier phases remain the source of
  truth.

## Architecture decisions

RUN_OUTPUT still requires a bound terminal Harness Run. Compare does not start a
third Run. `/admin/evaluations` stays for existing SDKs and operators.

## Validation

- `uv run pytest --no-cov` — 483 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase35_experience_eval.py`.
- `@obsion/sdk` Node tests — 21 passed, including Eval catalog/case/run/compare
  routes.
- Workbench at `http://localhost:3000`: sidebar **评测台** sits between Studio 开发台
  and 治理控制台. Composer still has one prompt (`向 Obsion 提问`) and no Agent picker.
- Eval catalog listed the existing `UI release gate 2026-08-25` dataset plus promoted
  Agent versions (builtins and `studio-ui-probe-agent` v1) and enabled model profiles
  (`reasoning-high` among them).
- Creating dataset `eval-ui-probe` returned 201. Adding a well-formed ROUTING case
  whose `fixtures` contained `actual: "fabricated"` returned HTTP 422
  `evaluation_expectation_unsupported` and showed
  `Evaluation cases cannot self-report fixtures.actual`. The dataset stayed empty.
- A ROUTING case without `fixtures.actual` (`route-knowledge-ui`) added successfully.
  Starting with `run_bindings {}` and revision `workbench-ui-1` completed **通过 · 1/1**.
- A second completed run `workbench-ui-2` on the same snapshot, compared against
  `workbench-ui-1`, showed **Agent 版本相同。回归 0 项。** Returning to 智能工作台 still
  has no Agent picker; the only combobox is 选择工作空间.

## Remaining risks

- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Vendor IM HTTP POST still requires a real tenant application. Phase 36 renders
  local-outbox envelopes without calling vendor APIs.
- Signed `1.0.0` remains operator-owned.
