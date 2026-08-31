# ADR 0058: No-Run idempotent writes use a durable two-transaction ledger

- Status: Accepted
- Date: 2026-08-31

## Context

Phase 77 correctly routed vendor Knowledge ingest/sync through the Capability Gateway
and declared them `IDEMPOTENT_WRITE`. The HTTP request UUID was used for correlation,
but no durable request claim existed. Retrying after a lost response could execute the
connector again and create another DocumentVersion. Keeping a claim in the same
transaction as connector execution would not solve process failure: a crash would
roll back both the business result and the evidence that an attempt had crossed an
external/object-storage boundary.

Fabricating a Harness Run or using Event Store as an idempotency database would
violate Workspace → Thread → Turn → Run → Step → Event. Reusing App Server request
rows would also mix transport-command responses with Capability execution semantics.

## Decision

Add `operator_capability_invocations`, a principal-scoped no-Run idempotency ledger
keyed by `(organization_id, principal_id, request_id)`. It stores only the Capability
and Connector pins, PolicyDecision, canonical input fingerprint, bounded status,
terminal Gateway result, safe error, lease, and retention timestamps. Raw input,
credentials, model context, Evidence, and Run identifiers are not stored.

For exact L2 `IDEMPOTENT_WRITE` operator calls, the Gateway now uses two durable
transactions:

1. Resolve/authorize/validate, claim the request UUID, and commit `IN_PROGRESS` before
   connector credential resolution or execution.
2. Execute the pinned connector and atomically commit Knowledge changes, Audit, and
   the immutable terminal result.

Exact terminal retries re-evaluate current Policy, then replay the stored result
without consuming rate, resolving credentials, or executing a connector. Reusing the
UUID with different canonical input fails with `idempotency_key_reused`. Concurrent
duplicates see `idempotency_request_in_progress`. An expired lease becomes `UNKNOWN`
with `operator_invocation_outcome_unknown`; it is never automatically retried.

The database permits only `IN_PROGRESS → COMPLETED|FAILED|UNKNOWN`, rejects terminal
updates, and rejects deletion before retention expiry. The admin projection and SDKs
expose metadata/status only; result and input payloads remain hidden. L1 browse
Capabilities remain outside the write ledger and continue to execute as ordinary
side-effect-free audited reads.

Vendor source operations require a UUID `X-Request-ID`. A safe but non-UUID generic
correlation value is rejected rather than silently replaced with an unreplayable key.
Operator ledger retention is independently configured by
`OBSION_OPERATOR_CAPABILITY_IDEMPOTENCY_RETENTION_HOURS` (seven days by default), not
by the shorter App Server command-retry window.

## Consequences

No-Run writes now have real idempotent response replay and fail-closed unknown-outcome
handling without adding a second Event protocol. A server-generated response
`X-Request-ID` can be reused by clients when they did not supply one.

UNKNOWN means reconciliation is required, not that the operation failed. The current
phase deliberately does not add a generic “retry unknown” mutation: a connector-
specific investigation must determine whether a new request UUID is safe. Production
writes remain forbidden and no Approval, Run Event, or Evidence is invented.
