# ADR 0081: IM delivery uses a leased Outbox and holds ambiguous outcomes

- Status: Accepted -- M1b control-plane implementation complete
- Date: 2026-09-09
- Milestone: M1b durable IM delivery
- Amends: ADR 0078 and ADR 0080

## Context

A final IM reply crosses a vendor boundary after the Harness Run has completed. A
process exit, timeout, malformed vendor response, or failure while saving a receipt
can leave it unknown whether the vendor accepted the message. Retrying that result
may duplicate a user-visible reply. The former synchronous delivery path had no
durable attempt ledger, fenced owner, or operator reconciliation record.

## Decision

`ImDelivery` remains the single durable delivery fact and gains a PostgreSQL Outbox
state machine. Only a policy-authorized, recipient-revalidated completed Run can
create or prepare a delivery. An explicit vendor adapter uses `obsion-im outbox` to
claim an eligible delivery with a channel-scoped lease and monotonically increasing
claim generation. The claim creates one immutable `ImDeliveryAttempt` with a stable
internal idempotency key.

- A worker may report `SENT` only with a nonempty vendor-issued message identifier.
- A known pre-send or rate-limit failure becomes `FAILED` with bounded backoff and
  may be claimed again only after its scheduled retry time.
- An uncertain result becomes `UNKNOWN`. It is not eligible for another vendor
  request. An administrator must record bounded evidence and either a vendor receipt
  (`SENT`) or `CONFIRMED_UNSENT` before a new delivery attempt can be eligible.
- Complete, fail, and unknown reports verify lease ownership and claim generation.
  A stale worker cannot complete an attempt reclaimed or expired under another
  generation.
- Delivery-time Policy, installation, principal binding, audience and content
  fingerprint checks run again before a worker receives text. Trusted groups remain
  status-only regardless of mutable Run context.

The adapter process holds vendor credentials only through its environment or the
approved secret injection path. The Agent never receives them. No generic HTTP
delivery or bypass around the Capability Gateway, Policy Engine, or Audit Writer is
introduced.

## Consequences

- Migration `d0e2f5a7b3c4` adds Outbox claim/reconciliation fields and attempts
  without replacing historical delivery rows.
- A vendor response without a receipt, a transport ambiguity, or receipt-recording
  loss is operational work for reconciliation, not a signal to resend.
- `obsion-im outbox --once` supports supervised readiness checks; a long-running
  `obsion-im outbox` worker is the deployable worker entry point. Both reject the
  local-outbox transport.
- M1c still requires a real DingTalk test tenant, Stream lifecycle exercise,
  compatibility HTTP convergence, and operator timing/receipt evidence. This ADR
  does not claim any live tenant result or production promotion.
