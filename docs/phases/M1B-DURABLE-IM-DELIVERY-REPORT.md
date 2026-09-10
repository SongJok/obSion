# M1b durable IM delivery implementation report

Status: COMPLETE for the repository-local M1b increment. Updated 2026-09-09.
This is not a DingTalk tenant validation, M1 completion, production-readiness claim,
or Phase 98 promotion.

## Implemented

- `ImDelivery` now has durable scheduled retry, rate-limited claim, owner lease,
  generation fence, recipient snapshot, reconciliation and exact vendor-receipt
  fields. `ImDeliveryAttempt` persists each vendor boundary attempt.
- `POST /api/v1/experience/im/deliveries/claims` gives an `im.delegate` worker one
  fresh channel claim. Completion, failure and unknown reporting require the current
  claim identity; inspection exposes status and attempts only to the subject or an
  authorized delegate.
- Missing receipts, transport uncertainty and receipt-recording loss become
  `UNKNOWN`. They cannot be retried until an `identity.write` administrator records
  bounded reconciliation evidence. Confirmed-unsent outcomes may return to the
  retry schedule; confirmed sent outcomes require a vendor message identifier.
- `obsion-im outbox` runs the vendor delivery worker and rejects local-outbox. The
  `--once` mode performs at most one eligible attempt for deployment checks.
- The existing synchronous adapter remains compatible for legacy channels that have
  no Outbox claim. Base `ImError` continues to use its historical failure envelope;
  explicit typed errors enter durable retry, rejected, pre-send, or UNKNOWN handling.

## Migration and rollback

Revision `d0e2f5a7b3c4` follows M1a revision `c9d1e4f6a2b3`. It is additive for
existing deliveries and introduces attempt rows and immutable state guards. Normal
rollback disables new workers and retains the ledger. An old schema cannot truthfully
represent `UNKNOWN`, attempt history or reconciliation data; reconcile and retain
those facts before any operator-approved destructive downgrade.

## Validation scope

Automated checks cover the state machine, fenced claims, exact receipts, known retry,
UNKNOWN no-retry, reconciliation, audience rechecks, legacy adapter compatibility,
the adapter worker, Stream normalization and command registration. The repository
full suite passed with `1106 passed, 29 skipped` on 2026-09-09. The skips are
opt-in PostgreSQL integration or migration tests and operator-owned live connector
tests; the exact aggregate result is recorded in `docs/project-status.yaml`.

## Remaining M1 gate

M1c must run the DingTalk Stream lifecycle against a non-production tenant, converge
the compatibility HTTP route on the same trusted admission endpoint, exercise vendor
redelivery and receipt loss, and retain redacted timing, Audit and reconciliation
evidence. Production promotion remains governed by Phase 98's separate operator
approval gates.
