# ADR 0015: Vendor IM outbound is local outbox envelopes

- Status: Superseded in part by ADR 0041 (Feishu HTTP only)
- Date: 2026-08-29

## Context

Phase 32 translates documented Feishu, DingTalk, and WeCom callback envelopes into the
IM ingest contract. Replies were a generic `OutboundMessage` written to an in-process
list, and `OutboundMessage.channel` was the transport name `development` even when the
inbound identity namespace was `feishu`. Goal.txt still lists 飞书 / 钉钉 / 企业微信 as
Experience clients of one App Server. Calling `open.feishu.cn`, `oapi.dingtalk.com`, or
`qyapi.weixin.qq.com` without a tenant application would fake an external integration.

## Decision

Vendor outbound is envelope rendering into a local outbox. `obsion-im` maps a completed
Run answer onto the documented reply body for the inbound identity namespace:

- Feishu: `receive_id` = inbound `chat_id`, `msg_type` = `text`
- DingTalk: `conversation_id` plus `msgtype`/`text.content`
- WeCom: `ToUserName` = inbound `FromUserName`, optional `ChatId`

Delivery is only `local_outbox`. `--deliver http`, `OBSION_IM_DELIVER=http`, and an
HTTP(S) `--outbox` path fail closed. Identity remains control-plane
`im_principal_bindings`. The adapter still does not import HTTP clients or vendor SDKs,
and source must not contain vendor endpoint strings. Optional `OBSION_IM_OUTBOX` appends
JSONL envelopes to a local file.

## Consequences

Operators can inspect the exact reply body that a later tenant HTTP client would send,
without pretending that HTTP already succeeded. Live webhook hosting, WeCom AES
decrypt, and vendor HTTP POST remain unimplemented until a real tenant application
exists. Phase 37 adds a loopback listener; public ingress and vendor HTTP POST remain
unimplemented.
