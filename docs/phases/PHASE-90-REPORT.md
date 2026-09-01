# Phase 90 report: per-stage investigation narrative

## What was implemented

Phase 90 turns the Runtime timeline into a per-stage investigation
narrative — the second deferred finding from the Phase 88 experience
audit — using only persisted correlation keys:

- **Step duration**: `completed_at − started_at` rendered per step
  (ms below one second, seconds above); missing timestamps render
  nothing.
- **Step evidence chips**: each step lists its Evidence rows (joined on
  the persisted `step_id`) as typed chips, bounded at six with an
  explicit overflow count; clicking opens the Phase 89 typed detail in
  the Evidence tab.
- **Claim badges**: claims whose `evidence_ids` intersect the step's
  evidence render as `结论 C{n}` badges jumping to the Claims tab,
  closing the step → evidence → conclusion chain.
- **Unattributed section**: evidence without a `step_id` (document
  attachments and similar Run-start rows) shows in an explicit
  "未关联步骤的证据" block instead of disappearing.

## Architecture decisions

ADR 0069 records the five decisions: persisted-key correlation only,
absence rendered as absence, unattributed evidence kept visible,
navigation reusing existing surfaces, and bounded display.

## Migration

None. Rendering-only change plus CSS; rollback is reverting the phase
commits.

## Validation

- Phase suite: `test_phase90_investigation_narrative.py` (7 tests) plus
  rolled-forward Phase 82-89 bookkeeping suites.
- Web typecheck, lint, and production build pass.
- Repository quality gate: `make check` (ruff, contracts, evaluations,
  release notes, candidate contract, datasets, secret scan, eslint,
  tsc, full pytest, node:test suites, alembic check).
- `make test-java` and `make validate-release-candidate-contract` pass;
  2 live ledgers, 2 drill ladders, 16 checks, 6 PENDING operator gates
  unchanged.

## Deferred findings still open

From the Phase 88 audit: post-conclusion context actions (view
code/logs/SQL, generate report, create issue), the operations analytics
loop, full admin CRUD, a schema-driven chart renderer, and a JavaScript
component-test stack for `apps/web`.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase changes rendering only and
does not advance promotion.
