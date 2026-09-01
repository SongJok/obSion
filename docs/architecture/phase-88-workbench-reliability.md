# Phase 88 Alpha.1 Workbench reliability architecture review

## Review question

Can the Workbench and experience clients be hardened against unbounded
requests, all-or-nothing degradation, invisible fallback, stale async state,
and canned governance text — without expanding the frozen Alpha.1 product
surface, adding a second runtime, or weakening any fail-closed boundary?

**Status: PASS for client-side reliability hardening; PENDING for all six
operator gates.**

## Invariants reviewed

- **Runtime architecture unchanged**: one Python control plane, one App
  Server, one Harness, Workspace → Thread → Turn → Run → Step → Event, and
  Capability Gateway → Policy → connector for every external access. This
  phase touches `apps/web`, `apps/desktop`, `apps/ide-extension`, tests, and
  documentation only; no API, schema, capability, policy, or runtime code
  changed.
- **Session and credential invariants hold**: `credentials: "include"` is
  preserved, no token is persisted in any browser storage, the Desktop
  secret-file and IDE Secret Storage boundaries are untouched, and the new
  route-level error surface logs only a diagnostic digest — never session
  or business data.
- **No invented data**: per-domain degradation falls back to empty
  projections that the UI already labels as persisted data; loading, empty,
  and no-match states are distinguished so "no data" is never shown while a
  request is in flight; a total outage (all domains failing) still raises
  the page-level error. Fail-closed is preserved where nothing can be
  verified.
- **No duplicate operator intent**: the request layer adds timeouts and
  normalization but deliberately no automatic retry — idempotent replay is
  the backend's durable contract (principal-scoped request keys), and a
  browser-side retry could re-submit a mutation the control plane already
  accepted.
- **Governance text is human**: preflight declarations and approval reasons
  are operator-entered (minimum length enforced) or the operation is
  cancelled; no canned string can enter an approval or audit record. This
  strengthens, rather than changes, the Phase 7 governed-action contract.
- **Streaming contract unchanged**: the App Server stream remains the
  primary channel and REST cursor reconciliation remains the compatibility
  path; the phase only makes the active path visible
  (live / polling / interrupted) and never widens what the transport can
  reach.

## Boundary confirmation

- The candidate contract, recorded evidence ledgers, drill ladders, and the
  six PENDING operator gates are untouched; nothing in this phase feeds
  `promotion_eligible`.
- Audit findings that imply new product surface (typed Evidence views,
  per-stage investigation narrative, post-conclusion context actions, full
  admin CRUD, a JavaScript component-test stack) were deliberately excluded
  and remain candidates for later phases with their own architecture
  reviews.

## Verification

- `services/control-plane/tests/test_phase88_workbench_reliability.py`
  (12 tests) pins every boundary above as static source assertions.
- Desktop and IDE `node:test` suites cover the new guard and
  approval-reason semantics (3 new IDE cases, 1 new Desktop case).
- `make check`, `make test-java`, and
  `make validate-release-candidate-contract` all pass on the final tree.
