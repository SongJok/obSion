# ADR 0078: Vendor IM delivery requires a vendor-issued receipt

- Status: Accepted
- Date: 2026-09-08

## Context

`ImDeliveryService` records a completed vendor delivery only after the Experience
adapter supplies a `vendor_message_id`. DingTalk's outbound adapter previously used
the local Obsion delivery UUID when a successful-looking vendor response omitted its
message identifier. That replaced an unknown external result with a local identifier
and made the audit trail look like a confirmed vendor delivery.

## Decision

DingTalk delivery succeeds only when its response has a non-empty vendor-issued
`messageId` or `message_id`. A response without either is an ambiguous outcome. The
adapter reports it as `UNKNOWN` through the governed delivery path; it does not
complete the persisted delivery, retry it blindly, or construct a substitute receipt.

The existing delivery UUID remains an internal idempotency and audit correlation key.
It is never a vendor receipt. A subsequent retry must preserve the control-plane
delivery contract and cannot claim success until the vendor returns its own receipt.

## Consequences

- Uncertain DingTalk outcomes remain visible as `UNKNOWN` for operator reconciliation.
- `im_deliveries.vendor_message_id` contains only a vendor-issued identifier.
- No database migration, new external write capability, credential change, or
  production promotion claim is introduced.
