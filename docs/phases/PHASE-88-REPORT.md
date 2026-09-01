# Phase 88 report: Alpha.1 Workbench reliability hardening

## What was implemented

Phase 88 acts on the experience-layer audit (goal.txt sections 55-58 and
86-87 against `apps/web`, `apps/desktop`, `apps/ide-extension`) and closes
its reliability findings without expanding the frozen Alpha.1 product
surface:

- **Bounded, normalized requests** (`apps/web/src/lib/api.ts`): every
  `fetch` composes `AbortSignal.timeout` (30s default, 120s for the six
  ingest/evaluation mutations) with any caller signal; timeout, abort,
  network, and JSON-parse failures normalize to `ApiError` codes
  `request_timeout`, `request_cancelled`, `network_error`, and
  `invalid_response`. No automatic retry is added — idempotent replay is
  the backend's durable contract.
- **Route-level boundaries**: `app/error.tsx` (client boundary using this
  Next.js version's `retry` prop, digest-only logging),
  `app/not-found.tsx`, `app/loading.tsx`, and matching `.route-fallback`
  styles.
- **Per-domain admin degradation** (`admin-view.tsx`): the 22 governance
  endpoints load through `Promise.allSettled`; failed domains are named in
  a `.notice.warning` banner with a retry action, healthy domains keep
  rendering, and only a total outage raises the page-level error.
- **Async correctness**: Eval results are generation-guarded
  (`resultsGeneration`) with a loading state and fully caught chains;
  `loadCases` no longer rejects unhandled; Data and Code views distinguish
  loading, empty, and no-match states (`searched` flag, loading badges).
- **Visible stream fallback**: `workbench.tsx` tracks
  `live / polling / interrupted` stream state through `pollRun` and the
  Runtime inspector renders it as a chip for non-terminal Runs, so the REST
  reconciliation path is no longer invisible.
- **No cross-Run attribution**: the inspector resets evidence/artifact
  detail selection when the inspected Run changes (render-time adjustment
  on `runId`) and the evidence detail footer names the owning Run.
- **Operator-entered governance text** (`actions-view.tsx`,
  `desktop/shell.ts`, `ide-extension/commands.ts`): the preflight
  declaration is a required operator-typed statement (minimum 10 chars)
  submitted through a confirmation modal; the Desktop shell guards every
  action with uniform error handling and pending-state button disabling and
  requires a non-empty approval reason; the IDE extension treats a
  dismissed reason prompt as a cancelled decision and rejects blank
  reasons. The canned "已核对…" / "Approved from IDE" / "Approved from
  Desktop" strings are gone.
- **Single-flight upload** (`knowledge-view.tsx`): concurrent uploads are
  blocked, the button reflects the pending state, and the file input
  re-arms after completion.

## Architecture decisions

ADR 0067 records the five decisions: bounded+normalized requests without
client-side retry; per-domain degradation that never invents data and stays
fail-closed on total outage; visible sync state; operator-entered or
cancelled governance declarations; and keeping the established
lint/typecheck/build + Python static boundary verification pattern instead
of bundling a new JavaScript test stack into a hardening phase.

## Migration

None. No schema, settings, API, or runtime changes; clients pick the
hardening up on their next build. Rollback is reverting the phase commits.

## Validation

- Phase suite: `test_phase88_workbench_reliability.py` (12 tests) plus
  rolled-forward Phase 82-87 bookkeeping suites.
- Client suites: `npm test --workspace @obsion/desktop` (17 tests, 1 new)
  and `npm test --workspace @obsion/ide-extension` (12 tests, 2 new).
- Repository quality gate: `make check` (format, lint, mypy, full pytest
  with coverage, migration-check) and `make test-java`.
- Release tooling: `make validate-release-candidate-contract` (2 live
  ledgers, 2 drill ladders, 16 checks, 6 PENDING operator gates unchanged).

## Deferred audit findings (explicitly out of scope)

The audit's feature-level findings remain candidates for later phases with
their own architecture reviews: typed Evidence views (metric/log/deployment
/git-diff/config-diff), a per-stage investigation narrative in the Runtime
panel, post-conclusion context actions (view code/logs/SQL, generate
report, create issue), the operations analytics loop, full admin CRUD, a
schema-driven chart renderer, and a JavaScript component-test stack for
`apps/web`.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment, UAT,
staging-scoped timed DR drill, live OIDC / secret-manager validation,
signed image/tag, external publication). This phase changes client code
only and does not advance promotion.
