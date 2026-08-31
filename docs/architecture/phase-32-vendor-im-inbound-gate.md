# Phase 32 vendor IM inbound review

## Review question

Can documented Feishu, DingTalk, and WeCom callback envelopes become `InboundMessage`
values, resolve through control-plane principal mapping, and reach one App Server
without implementing vendor HTTP clients, a second Harness, or nickname authorization?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `feishu`, `dingtalk`, and `wecom` are inbound identity namespaces. Aliases `lark`,
  `dingding`, and `wechat_work` normalize to those names.
- Vendor ingest requires `--envelope`. Development ingest still uses conversation,
  text, and sender-id flags.
- Feishu uses `open_id`. DingTalk uses `senderStaffId`. WeCom uses `FromUserName`.
  Nicknames cannot authorize.
- Feishu `url_verification` returns the challenge, creates no Turn, and does not
  require a token.
- `OBSION_IM_WEBHOOK_SECRET` is optional. When set, inbound signatures are verified.
  When unset, unsigned fixtures are accepted.
- Config files must not contain vendor app credentials. The adapter must not import
  HTTP clients or vendor SDKs, and must not contain vendor endpoint strings.
- Outbound remains the local outbox. Phase 36 adds vendor-shaped reply envelopes
  without HTTP POST.
- Workbench administration manages `im_principal_bindings`.

## Automated acceptance map

- `apps/im-adapter/tests/test_im_envelopes.py` covers envelope translation, nickname
  rejection, aliases, and local HMAC verification.
- `apps/im-adapter/tests/test_im_config.py` accepts vendor namespaces, rejects unknown
  channels, and rejects nested vendor secrets in TOML.
- `apps/im-adapter/tests/test_im_main.py` requires vendor `--envelope`, proves URL
  verification without a token, and rejects invalid JSON.
- `apps/im-adapter/tests/test_im_architecture.py` forbids Harness, HTTP clients, vendor
  SDKs, and vendor endpoints.
- `services/control-plane/tests/test_phase32_vendor_im_inbound.py` ingests a Feishu
  envelope as the bound User and rejects a DingTalk nickname as `unknown_im_sender`.

## Human review checklist

- Confirm operators bind stable vendor user ids before pointing a live webhook at
  this adapter.
- Confirm `OBSION_IM_WEBHOOK_SECRET` is set in any environment that accepts untrusted
  inbound envelopes.
- Confirm vendor HTTP POST is still absent until a tenant application exists.
