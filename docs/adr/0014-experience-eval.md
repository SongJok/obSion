# ADR 0014: Eval is a governed evaluation console

- Status: Accepted
- Date: 2026-08-29

## Context

Goal.txt calls for Obsion Eval as an Agent evaluation product. The control plane
already stores Golden Datasets, executes ROUTING / SQL_POLICY / RUN_OUTPUT
evaluators, pins Agent and model-profile versions, and compares immutable baselines.
Those APIs lived under `/api/v1/admin/evaluations`. Workbench only showed a short
gate list in the governance console. Operators could still POST `fixtures.actual` on a
well-formed case because file-manifest validation was the only place that rejected
self-reported answers.

Eval must not become a second Harness, must not fabricate RUN_OUTPUT, and must not
present an Agent picker in conversation.

## Decision

Eval is a Workbench developer surface plus `/api/v1/eval` REST. It wraps
`EvaluationService`. It does not import Harness, Capability Gateway, or Model Gateway.
`evaluations.read` lists catalog, cases, runs, and results. `evaluations.write`
creates datasets, cases, and Evaluation Runs. Engineer retains both; Analyst remains
read-only; Admin retains `*`.

`fixtures.actual` is rejected by the shared case contract used by both `/eval` and
`/admin/evaluations`. RUN_OUTPUT still requires `run_ref` or `run_id` plus
`run_bindings` to a terminal Harness Run. Compare reuses the existing baseline
snapshot rule: two completed runs on the same dataset fingerprint, without starting a
third Run. Agent runtime rollback stays Studio promote.

The Workbench **评测台** is a dataset/run console. Composer still has one assistant.

## Consequences

Release evidence can be created and compared from Workbench without editing JSON
files. Self-reported answers fail closed on the API. Existing `/admin/evaluations`
routes remain. Staging and security sign-off stay operator-owned.
