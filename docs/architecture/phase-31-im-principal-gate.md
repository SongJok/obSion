# Phase 31 IM principal mapping review

## Review question

Does an inbound IM message resolve `(channel, sender_id)` to a provisioned User before
creating a Turn, reject nicknames as identity, fail closed when unmapped, and keep the
IM adapter as an Experience client of that control-plane decision?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `im_principal_bindings` is organization-scoped. The unique identity key is
  `(organization_id, channel, sender_id)`.
- Binding requires `identity.write`. Ingest requires `im.delegate`.
- Unmapped or revoked senders return `unknown_im_sender` (403).
- `sender_display` is ignored for authorization. Extra identity fields such as
  `display_name` are schema-forbidden.
- After mapping, `Turn.created_by` is the bound User. The bot is not the Turn owner.
- `obsion-im` calls `POST /api/v1/experience/im/messages` and waits for the Run. It
  does not call `turn.create` / `runtime.ask` and does not import Harness.
- `feishu` / `dingtalk` / `wecom` are identity namespace labels only. No vendor HTTP
  client is implemented.

## Automated acceptance map

- `services/control-plane/tests/test_phase31_im_principal_mapping.py` covers bound
  ingest, fail-closed unknown senders, schema rejection of display names, revocation,
  namespace tags, and missing `im.delegate`.
- `apps/im-adapter/tests/test_im_bridge.py` proves REST identity ingest rather than
  App Server `turn.create`.
- `apps/im-adapter/tests/test_im_architecture.py` forbids a second runtime and requires
  `create_im_message`.
- Error catalog origins cover `im_delegate_denied`, `im_sender_id_required`, and
  `unknown_im_sender`.

## Human review checklist

- Confirm bot service accounts receive `im.delegate` without `*`.
- Confirm operators bind production IM user ids before pointing a vendor adapter at a
  live tenant.
- Confirm nicknames never appear in binding administration as identity keys.
