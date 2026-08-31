# ADR 0041: Feishu HTTP is an explicit Experience delivery transport

- Status: Accepted
- Date: 2026-08-30
- Supersedes: ADR 0015 and ADR 0016 only for Feishu outbound HTTP
- Amended by: ADR 0047 for DingTalk and WeCom HTTP

## Context

Phases 32, 36, and 37 translate Feishu, DingTalk, and WeCom envelopes and render
local-outbox replies. Those ADRs forbade vendor HTTP because no tenant application
existed. A real Feishu application is now available through operator-owned
environment variables. Pretending that generic `--deliver http` talks to DingTalk or
WeCom, or putting `app_secret` in TOML, would fake integrations or leak credentials.

Feishu message send is not an Agent Capability. The Agent never receives vendor
credentials. The Experience adapter delivers the already-completed Run answer to the
user's chat, the same way Web streams `answer.delta`.

## Decision

`obsion-im --deliver feishu-http --channel feishu` is the Feishu vendor HTTP
transport. It:

- reads `OBSION_FEISHU_APP_ID` and `OBSION_FEISHU_APP_SECRET` from the environment;
- obtains `tenant_access_token` from `https://open.feishu.cn`;
- sends the control-plane-authorized final answer as `im/v1/messages` with
  `receive_id_type=chat_id` and Feishu `uuid` idempotency;
- never imports `lark_oapi` or other vendor SDKs.

Generic `--deliver http` remains fail-closed. Config files still reject vendor
secrets. WeCom AES ciphertext still fails closed. DingTalk and WeCom HTTP are
defined separately in ADR 0047 under the same Experience and Policy invariants.

Before POST, the adapter calls `POST /api/v1/experience/im/runs/{id}/deliveries`.
The control plane authorizes `im.reply.deliver` through Policy Engine, pins the
answer fingerprint, and records PENDING/SENT/FAILED receipts. A later HTTP client
cannot invent a different conversation or answer.

## Consequences

Operators can deliver Feishu replies without a second Harness. Local outbox remains
the default and the inspectable fallback. Public ingress and WeCom AES stay
operator-owned concerns outside this ADR.
