# PHASE-81-REPORT — Feishu live reply validation

## What was implemented

- Added `FeishuClient.list_chats`: read-only bot chat discovery, bounded to one
  vendor page of at most 100 items, tenant-token authorized, fail-closed on
  malformed items, with credential/token redaction on vendor errors. The same
  listing is exposed on `FeishuHttpChannel`.
- Extended `make validate-feishu-live` from three to four non-sending probes by
  registering a live bot-chat-listing probe under the existing strict `live`
  marker.
- Added the strict `feishu_send_live` pytest marker and
  `make validate-feishu-send-live`. The target requires `OBSION_FEISHU_SEND_LIVE=1`,
  environment-provided app credentials, and an explicit
  `OBSION_FEISHU_LIVE_CHAT_ID`, then delivers exactly one clearly marked probe
  message through the production `FeishuHttpChannel.reply` contract and asserts
  the vendor message id.
- Documented `OBSION_FEISHU_LIVE_CHAT_ID` in `.env.example`, the runbook live
  validation ladder, ADR 0060, and the `0.81.0-dev` machine/human release
  contracts, which are now the CLI default.
- Hardened `FeishuClient` after the first live run: HTTP 400 business envelopes
  are now size-bounded and parsed before status classification, 401/403 and the
  documented denied vendor codes classify as `FeishuDeniedError`, and bearer
  tokens are redacted from envelope errors on the non-2xx path.
- Froze the `0.80.0-alpha.1` manifest as a static historical contract: its
  live-tree evidence binding was validated at the Alpha.1 candidate commit and
  the narrative is preserved in `0.80.0-alpha.1.md`.

## Architecture decisions

ADR 0060 keeps live send validation opt-in, single-message, and fail-closed. The
send probe never auto-discovers a target, never counts a skip as a pass, and is
transport validation only: it creates no Run, Turn, Event, Evidence, Approval, or
audit row, and the production delivery path still requires a Policy-authorized
receipt. DingTalk and WeCom live delivery remain unimplemented.

## Migration

No database or Event migration is added. The manifest declares
`migration.database: none` and Alembic drift checks continue to pass.

## Validation

- `make check` passed: Ruff format/lint, strict mypy, contract/Event/evaluation
  validation, release-note validation against the new `0.81.0-dev` default
  manifest, dataset execution, zero secret findings, frontend lint/typecheck, the
  full Python suite, Desktop/IDE/TypeScript SDK tests, and Alembic drift.
- `apps/im-adapter/tests/test_feishu.py` — bounded listing, page-size limits,
  missing/malformed items, error redaction, HTTP 400 business-envelope
  classification, denied classification, and both opt-in probes: 13 passed
  with 3 explicit live skips.
- `test_phase81_feishu_live_reply.py` — target gating, marker registration,
  manifest validity, CLI default, and project-status tracking: 5 passed.
- `test_phase80_alpha1_release.py` and `test_phase76_feishu_live_validation.py` —
  frozen Alpha.1 contract and the four-probe non-sending gate: 8 passed.
- Live tenant runs with operator process credentials (never printed or
  persisted): `make validate-feishu-live` 4 passed, including a real
  scope-denied chat listing classified as `FeishuDeniedError` (code 99991672)
  on the read-only app; `make validate-feishu-browse-live` 1 passed.
- Operator-run live end-to-end delivery on the agent app: an ephemeral bot-owned
  chat was created, one marked probe message was delivered through the
  production `FeishuHttpChannel.reply` path, and the vendor returned message id
  `om_x100b6667a77650a8de3f0f27b373c4c`. Disband was denied (scopes `im:chat` /
  `im:chat:delete` not granted), so the empty bot-only chat
  `oc_e9eeff464e4e3250ea411c1d74b5059e` remains for manual cleanup.
- `make validate-feishu-send-live` gate verified fail-closed: without
  `OBSION_FEISHU_LIVE_CHAT_ID` it exits 2 before any vendor call.

## Remaining risks

- `make validate-feishu-send-live` requires an operator-chosen chat where the bot
  is a member; without `OBSION_FEISHU_LIVE_CHAT_ID` the probe skips and is not
  counted as a pass.
- The live tenant's read-only app lacks `im:chat*` scopes (denials classify
  correctly); the agent app can deliver but cannot disband chats without the
  `im:chat`/`im:chat:delete` scope, so ephemeral-chat cleanup is manual.
- DingTalk and WeCom live delivery validation remains operator-owned manual
  procedure.
- External publication, clean staging, UAT, timed DR, live OIDC/secret manager,
  and human security/data-owner sign-off remain operator-owned.
