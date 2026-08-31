# ADR 0047: DingTalk and WeCom HTTP are explicit Experience delivery transports

- Status: Accepted
- Date: 2026-08-30
- Amends: ADR 0041 (Feishu-only live vendor HTTP)

## Context

Phase 62 introduced governed Feishu HTTP delivery. DingTalk and WeCom already had
inbound envelope parsing and local-outbox reply shapes, but outbound HTTP stayed
fail-closed because pretending generic `--deliver http` talked to those vendors
would fake integrations. Operators now need the same Experience pattern for all
three identity namespaces without giving Agents vendor credentials or introducing
a second Harness.

## Decision

`obsion-im` gains two explicit delivery transports:

- `--deliver dingtalk-http --channel dingtalk` with
  `OBSION_DINGTALK_APP_KEY` / `OBSION_DINGTALK_APP_SECRET`, pinned to
  `https://oapi.dingtalk.com`, token via `/gettoken`, send via `/chat/send`.
- `--deliver wecom-http --channel wecom` with
  `OBSION_WECOM_CORP_ID` / `OBSION_WECOM_CORP_SECRET` / `OBSION_WECOM_AGENT_ID`,
  pinned to `https://qyapi.weixin.qq.com`. Group chats use `/cgi-bin/appchat/send`;
  direct chats use `/cgi-bin/message/send`.

Shared invariants with Feishu HTTP:

- Before POST, call `POST /api/v1/experience/im/runs/{id}/deliveries`.
- Policy authorizes `im.reply.deliver`; answer fingerprint is pinned.
- Config files still reject vendor secrets.
- Generic `--deliver http` remains rejected.
- No vendor SDKs (`lark_oapi`, DingTalk SDKs, `wechatpy`).
- WeCom AES ciphertext decrypt remains unimplemented and fail-closed.

## Consequences

Feishu, DingTalk, and WeCom share one Experience delivery ledger. Live tenant
exercise still depends on operator-owned credentials. WeCom EncodingAESKey
decrypt and public DingTalk/WeCom ingress stay later phases.
