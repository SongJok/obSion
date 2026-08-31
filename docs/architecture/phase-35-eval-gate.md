# Phase 35 Experience Eval review

## Review question

Can engineers run Golden Dataset evaluations, bind real terminal Runs, and compare
two completed Evaluation Runs from Workbench Eval without a second Harness, without
an Agent picker in conversation, and without accepting `fixtures.actual`?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `/api/v1/eval` wraps `EvaluationService`. Admin evaluation routes remain.
- `evaluations.read` lists catalog, cases, runs, and results.
  `evaluations.write` creates datasets, cases, and runs.
- Catalog exposes promoted Agent versions and enabled model profiles for pinning.
- `fixtures.actual` returns `evaluation_expectation_unsupported`.
- Compare requires two completed runs on the same dataset snapshot.
- Workbench **评测台** is a developer console. Composer has no Agent picker.
- Eval application code does not import Harness, Capability Gateway, or Model
  Gateway.

## Automated acceptance map

- `test_phase35_experience_eval.py` covers architecture, secret-free actual
  rejection, compare, and authorization.
- Python and TypeScript SDKs wrap the Eval routes.
- Shared case contract also rejects `fixtures.actual` on `/admin/evaluations`.

## Local Workbench verification

Local `http://localhost:3000` **评测台** created `eval-ui-probe`, rejected
`fixtures.actual` with `evaluation_expectation_unsupported`, completed two ROUTING
runs (`workbench-ui-1` / `workbench-ui-2`, both 1/1), compared them with 0
regressions, and left Composer without an Agent picker. This does not replace
staging, UAT, or security sign-off.

## Human review checklist

- Confirm Engineer/Analyst ownership of Eval in the tenant IdP mapping.
- Confirm operators bind `run_ref` to real terminal Runs before RUN_OUTPUT gates.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
