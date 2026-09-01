# Phase 96 report: post-conclusion context actions

## What was implemented

The gap audit's "post-conclusion context actions" item is closed —
the investigation loop now continues into collaboration without
leaving the Runtime inspector:

- **Per-claim actions**: each Critic-verified claim on a completed run
  offers 转为任务 and 记录决策.
- **Prefilled payloads**: pure builders compose the title (`结论
  C1：…`), the statement, a provenance line, the verification status,
  and evidence-cited rationale; the modal lets the operator edit
  before saving.
- **Provenance preserved**: every created task/decision carries
  `source_run_id`, so Phase 92's provenance chips and Runtime
  inspector links render immediately.
- **Workspace pinning**: the target workspace comes from
  `run.workspace_context.workspace_id`, with the backend's
  `workspace_source_run_mismatch` rejection mapped to an actionable
  message.
- **Navigation**: "在协作中查看" closes the inspector and opens the
  collaboration view.

## Architecture decisions

ADR 0075 records the five decisions: actions per claim, completed
runs only, prefilled editable payloads, workspace from the run, and
navigation reuse.

## Migration

None. No backend route, schema, or permission change. Rollback is
reverting the phase commits.

## Validation

- `apps/web/tests/claim-actions.test.ts` — 8 vitest cases over the
  pure builders.
- `services/control-plane/tests/test_phase96_claim_actions.py` — 6
  tests: live payload acceptance with same-workspace provenance,
  cross-workspace rejection, static Web wiring, and bookkeeping.
- `make check` and `make test-java` pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Code Intelligence cross-language precision (P3), the operations
analytics loop, and full admin CRUD remain candidates.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase connects investigation to
collaboration and does not advance promotion.
