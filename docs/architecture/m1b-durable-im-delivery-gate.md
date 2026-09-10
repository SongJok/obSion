# M1b durable IM delivery architecture gate

## Review question

Can a completed IM Run use one durable, policy-controlled delivery record with
fenced Outbox attempts, bounded retry, exact vendor receipts, and no blind retry
after an ambiguous external outcome?

**Status: PASSED for repository-local M1b scope.** The delivery state machine,
forward migration, adapter Outbox worker, compatibility path, and local tests are
implemented. DingTalk live Stream/receipt evidence remains an M1c operator gate.

## Required path

```text
completed Run + recipient snapshot + Policy
  -> ImDelivery (PENDING)
  -> channel rate limit + leased, fenced ImDeliveryAttempt
  -> explicit vendor adapter request
  -> vendor receipt => SENT
     known pre-send failure => FAILED + bounded backoff
     uncertain external outcome => UNKNOWN + administrator reconciliation
```

- `ImDelivery` is the sole delivery fact. `ImDeliveryAttempt` is an append-only
  execution record; it is not a second Harness trajectory.
- Claims lock one organization/channel queue, use `SKIP LOCKED`, a lease owner and
  increasing generation. Completion and failure reject stale owners.
- The vendor request only follows fresh recipient, installation, binding, audience,
  Policy, Run completion and content fingerprint checks. A trusted group receives
  a content-free status even if a mutable Run context says otherwise.
- The worker consumes an explicit Feishu/DingTalk/WeCom transport. Local-outbox
  cannot accidentally make an external claim. The internal delivery UUID is passed
  as correlation/idempotency data but cannot substitute for a vendor receipt.
- `UNKNOWN` requires administrator evidence for `SENT` or `CONFIRMED_UNSENT`; it
  cannot return to the claimable queue without that recorded decision.

## Repository-local evidence

- Revision `d0e2f5a7b3c4` extends the M1a schema with delivery claim/reconciliation
  columns, `im_delivery_attempts`, organization-bound foreign keys, status and
  immutability guards, unique ordinal attempts, and unique channel/vendor receipts.
- `ImDeliveryService` exposes preparation, claim, complete, known-failure,
  UNKNOWN, reconciliation, delivery status and attempt inspection through the
  authenticated Experience API. All terminal changes emit Audit records.
- `ImOutboxWorker` validates claim lineage and content fingerprints, classifies
  deterministic vendor failures separately from ambiguous outcomes, and reports the
  vendor-issued receipt with the fenced claim generation.
- Automated tests cover receiver revocation, trusted-group status-only delivery,
  legacy synchronous compatibility, exact receipt requirements, claims, retry
  scheduling, lease expiry, fenced stale reports, UNKNOWN reconciliation and
  vendor-worker classification. The full suite remains the authoritative aggregate.

## Operator gate still open

Run the worker with credentials injected by the approved secret manager, then retain
redacted test-tenant evidence for allow, deny, rate limit, interrupted worker,
receipt loss, `UNKNOWN` reconciliation, cancellation, acknowledgement latency and
first-status latency. This gate does not accept mock receipts, local Delivery IDs or
an ONLINE DingTalk robot as proof.
