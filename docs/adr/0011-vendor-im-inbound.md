# ADR 0011: Vendor IM inbound is envelope translation, not HTTP

- Status: Accepted
- Date: 2026-08-29

## Context

Phase 30 implemented `obsion-im` as an Experience client. Phase 31 bound
`(channel, sender_id)` to provisioned Users. Channel names `feishu`, `dingtalk`, and
`wecom` were already legal identity namespaces, but the adapter rejected them because
a live tenant application does not exist in this environment. Pretending to call
`open.feishu.cn`, `oapi.dingtalk.com`, or `qyapi.weixin.qq.com` would fake an external
integration. Operators still need a documented path from vendor callback JSON/XML to
the same ingest contract, and a Workbench surface for the binding table.

## Decision

Vendor channel names are inbound identity namespaces. `obsion-im` translates documented
callback envelopes into `InboundMessage` and then calls
`POST /api/v1/experience/im/messages`. It does not host a public webhook server, does
not import vendor SDKs, and does not perform vendor HTTP.

Stable sender fields are the only identity keys:

- Feishu: `open_id` (fallback `user_id`)
- DingTalk: `senderStaffId`
- WeCom: `FromUserName`

Nicknames such as DingTalk `senderNick` may be copied to `sender_display` and have zero
authorization weight. Feishu `url_verification` returns the challenge without creating
a Turn and without requiring `OBSION_TOKEN`. Optional `OBSION_IM_WEBHOOK_SECRET`
verifies a local signature wrapper; when unset, unsigned fixtures are accepted for
development. Config files must not contain vendor app credentials. Outbound replies
remain the development-channel local outbox until Phase 36, which renders vendor-shaped
local-outbox replies without HTTP POST.

Workbench administration lists, creates, and revokes `im_principal_bindings`.

## Consequences

Inbound translation can be tested without a tenant app. Phase 36 renders vendor-shaped
local-outbox replies without HTTP. Live webhook hosting, WeCom AES ciphertext decrypt,
and vendor HTTP POST remain unimplemented until a real tenant application exists.
Outbound reuses `im_principal_bindings` and must not invent a second identity path.
