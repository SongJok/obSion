# ADR 0060: Live Feishu reply validation is opt-in, single-message, and fail-closed

- Status: Accepted
- Date: 2026-08-31

## Context

ADR 0055 established non-sending live Feishu probes and Phase 78 added a live
non-writing Capability Gateway browse. The remaining unvalidated leg of the IM
Experience path is real vendor delivery: the `feishu-http` channel contract
(vendor namespace check, delivery pinning, idempotency key, bounded client,
redacted errors) had only been exercised against mocked transports. Operators
validating an Alpha.1 deployment need a safe way to prove that a final answer can
actually reach a Feishu chat without standing up a full Harness loop.

Sending any message is a side effect, so the posture that worked for read probes
(ADR 0055) is insufficient on its own: a send target must never be guessed, and a
send probe must never look like a Harness Run, a Policy decision, or Evidence.

## Decision

Add a strict pytest `feishu_send_live` marker and `make validate-feishu-send-live`.
The target requires `OBSION_FEISHU_SEND_LIVE=1`, environment-provided app
credentials, and an explicit `OBSION_FEISHU_LIVE_CHAT_ID`. It delivers exactly one
clearly marked probe message through the production `FeishuHttpChannel.reply`
contract and asserts a vendor message id. Without any of the gates the probe skips
and performs no vendor call; a skip is never counted as a pass.

`FeishuClient.list_chats` provides read-only, single-page-bounded bot chat
discovery so operators can choose a target chat. It runs under the existing `live`
marker as a fourth non-sending probe; the send probe itself never auto-discovers
a target. The first live run also proved that Feishu answers scope denials with
HTTP 400 business envelopes (code `99991672`), so `FeishuClient` now parses the
size-bounded envelope before status classification and raises `FeishuDeniedError`
for 401/403 and the documented denied vendor codes, with bearer tokens redacted
from envelope messages on the non-2xx path.

The live send probe is transport validation only. It does not create a Run, Turn,
Event, Evidence, Approval, or audit row, and it does not weaken the production
path where replies are delivered only after a Policy-authorized delivery receipt.

## Consequences

- `validate-feishu-live` now runs four non-sending probes instead of three.
- `OBSION_FEISHU_LIVE_CHAT_ID` is documented configuration, not a credential.
- DingTalk and WeCom live delivery remain unimplemented; their validation stays
  operator-owned manual procedure.
- No runtime, Event, API, database, Agent, Capability, production write, or
  credential boundary changes in this ADR.
