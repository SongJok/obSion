# ADR 0067: Alpha.1 Workbench reliability hardening

## Status

Accepted (Phase 88, 0.88.0-dev)

## Context

The Phase 88 pre-work audit of `apps/web`, `apps/desktop`, and
`apps/ide-extension` against goal.txt sections 55-58 and 86-87 found the
experience layer functionally complete but carrying four classes of
reliability risk:

1. **Unbounded and unnormalized requests**: `lib/api.ts` used raw `fetch`
   with no timeout, no abort handling, and no normalization for network
   failures or non-JSON success bodies, so a hung connection could pin a
   view forever and low-level errors surfaced inconsistently.
2. **All-or-nothing degradation**: the admin console awaited one
   `Promise.all` over 22 governance endpoints; any single failing domain
   (costs, knowledge, audit, …) blanked the entire console.
3. **Invisible fallback and stale async state**: when the App Server stream
   cannot connect, the Workbench silently fell back to REST polling; Eval
   results loaded from event handlers could reject unhandled or overwrite a
   newer selection; the Runtime inspector kept a previous Run's detail
   selection visible after the inspected Run changed.
4. **Canned governance text**: the governed-action preflight submitted a
   hardcoded verification declaration, and the IDE/Desktop clients
   substituted "Approved from IDE" / "Approved from Desktop" when the
   operator dismissed the reason prompt — text that would land in approval
   and audit records without any human confirmation.

Two constraints shaped the response: the Alpha.1 product surface is frozen
(the candidate contract binds requirement coverage to existing evidence),
and the repository verifies the frontend through lint, typecheck,
production build, and Python static boundary tests rather than a JavaScript
component-test stack.

## Decision

1. **Every request is bounded and normalized.** `request()` composes
   `AbortSignal.timeout` (30s default; 120s for document/vendor ingest and
   evaluation mutations, which legitimately take longer) with any caller
   signal, and converts timeout, abort, network, and JSON-parse failures
   into `ApiError` with stable codes (`request_timeout`,
   `request_cancelled`, `network_error`, `invalid_response`). No retry is
   added: automatic retries on a governed control plane can duplicate
   operator intent, and idempotent replay is the backend's contract, not
   the browser's.
2. **Degrade per domain, never invent data.** The admin console loads each
   governance domain independently (`Promise.allSettled`), names failed
   domains in a warning banner with a retry action, and falls back to empty
   projections — which the UI already labels as real persisted data — only
   for domains that failed. A total outage (all domains failing) still
   raises the page-level error, preserving fail-closed behavior when
   nothing can be verified.
3. **Make the sync state visible.** The Runtime inspector shows a
   live / polling / interrupted chip for non-terminal Runs instead of
   hiding the REST reconciliation path, and detail selection resets when
   the inspected Run changes so evidence is never attributed across Runs.
4. **Governance declarations are operator-entered or the operation is
   cancelled.** The preflight declaration, approval reasons, and rejection
   reasons must be typed by a human (minimum length enforced); dismissing
   the prompt cancels the operation. No client may substitute canned text
   into an approval or audit record.
5. **Keep the established verification pattern.** The new behavior is
   pinned by `test_phase88_workbench_reliability.py` static boundary tests
   plus the existing lint/typecheck/build gate and the Desktop/IDE
   `node:test` suites; introducing a JavaScript component-test stack is
   deferred as a separate decision rather than bundled into a hardening
   phase.

## Consequences

- A hung control-plane connection now surfaces as a named error within 30s
  (120s for long mutations) instead of pinning a view indefinitely.
- One failing governance endpoint can no longer blank the admin console;
  operators see which domain failed and can retry without losing the rest.
- Users can tell whether run events arrive live or via polling, and whether
  synchronization is degraded while the run continues in the background.
- Approval and audit records now contain only text a human actually
  entered; cancelling a reason prompt cancels the decision.
- The frozen Alpha.1 surface is unchanged: no API, schema, capability, or
  runtime modification, no new dependency, and nothing feeds
  `promotion_eligible`. The larger audit findings (typed Evidence views,
  per-stage investigation narrative, post-conclusion context actions, full
  admin CRUD) remain candidates for later phases with their own ADRs.
