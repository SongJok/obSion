# PHASE-32-REPORT — Vendor IM inbound envelopes

## What was implemented

Phase 32 translates documented Feishu, DingTalk, and WeCom callback envelopes into the
existing IM ingest contract. The adapter remains an Experience client. It does not
implement a second Harness and does not call vendor HTTP APIs.

- `obsion-im --channel feishu|dingtalk|wecom ingest --envelope '{...}'` parses the
  documented callback body, verifies an optional local signature, and submits
  `POST /api/v1/experience/im/messages`.
- Stable sender fields (`open_id`, `senderStaffId`, `FromUserName`) are the identity
  keys. Nicknames remain display-only.
- Feishu URL verification returns the challenge without a token and without a Turn.
- Outbound replies still go to the development-channel local outbox.
- Workbench administration lists, creates, and revokes IM principal bindings.

## Architecture decisions

Vendor names are inbound identity namespaces, not HTTP clients. ADR 0011 records that
boundary. Config files cannot store vendor app secrets; `OBSION_TOKEN` and optional
`OBSION_IM_WEBHOOK_SECRET` stay in the environment.

## Validation

- `uv run pytest --no-cov` — 472 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase32_vendor_im_inbound.py` and the IM adapter envelope, config, main, and
  architecture tests.
- Bound Feishu ingest: `Turn.created_by` equals the mapped User.
- DingTalk nickname as `sender_id`: HTTP 403 `unknown_im_sender`.
- Architecture AST: IM sources do not import control-plane modules, HTTP clients, or
  vendor SDKs, and do not contain vendor endpoint strings.
- Workbench administration exposes channel / sender_id / user binding with explicit
  copy that nicknames cannot authorize. Browser verification on the live Workbench
  bound `feishu:ou_alice` to Local Administrator (HTTP 201) and revoked it (HTTP 200).

## Remaining risks

- Live webhook hosting, WeCom AES decrypt, and vendor HTTP POST require a real tenant
  application. Phase 36 renders vendor-shaped local-outbox replies without HTTP.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
