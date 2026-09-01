# Phase 91 report: JavaScript component-test stack for apps/web

## What was implemented

Phase 91 executes the decision ADR 0067 deferred — apps/web gains a real
executable test stack, closing the last workspace without one and
serving the standing goal of testing every feature:

- **Stack**: vitest 4.1.11, jsdom 29.1.1, @testing-library/react 16.3.3,
  @testing-library/jest-dom 7.0.1 — exact-pinned devDependencies;
  `vitest.config.mts` with `@/` alias, jsdom environment, scoped
  discovery, globals off.
- **Suites (34 tests)**:
  - `tests/typed-evidence.test.ts` — classifier dispatch for all seven
    envelopes plus generic fallback, precedence, and every no-invention
    accessor (including circular-object serialization and attribute
    bounds).
  - `tests/knowledge-citation.test.ts` — hits mapping, top-level
    fallback, blank-field omission, citation label defaults.
  - `tests/api.test.ts` — timeout/cancel/network/parse normalization,
    control-plane error passthrough with correlation ids, 204 handling,
    and the `credentials: "include"` / `no-store` transport invariants.
  - `tests/evidence-content.test.tsx` — rendered output for events, git
    diff, config diff, bounded tables, code symbols, citations, raw JSON
    fallback, and the metadata ledger (including the null-step row).
- **Wiring**: the workspace `test` script joins the root
  `npm test --workspaces --if-present` fan-out, so `make test` and CI
  run it with no workflow change.

## Architecture decisions

ADR 0070 records the five decisions: dev-only exact-pinned stack, tests
under `apps/web/tests/` with app-identical typing, behavior-level
coverage instead of snapshots, Python static suites retained, and zero
wiring changes.

## Migration

None. Dev-only change; rollback is reverting the phase commits.

## Validation

- `npm test --workspace @obsion/web` — 34/34 pass; root `npm test`
  passes across all workspaces.
- Phase suite: `test_phase91_web_test_stack.py` (7 tests) plus
  rolled-forward Phase 81-90 bookkeeping suites.
- Web typecheck, lint, and production build pass.
- `make check` (960 pytest, all node suites, eslint, tsc, alembic) and
  `make test-java` pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Post-conclusion context actions, the operations analytics loop, full
admin CRUD, and a schema-driven chart renderer remain candidates; broad
Workbench interaction coverage is a later increment on this stack.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase adds dev-only tests and does
not advance promotion.
