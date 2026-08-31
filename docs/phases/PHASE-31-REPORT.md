# PHASE-31-REPORT — IM principal mapping

## What was implemented

Phase 31 binds stable IM sender ids to provisioned Users inside the control plane.
The IM adapter remains an Experience client. It does not become a second Harness and
does not speak Feishu, DingTalk, or WeCom HTTP.

- Table `im_principal_bindings` maps `(channel, sender_id)` to `users.id`.
- Administration: `POST/GET /api/v1/admin/im-bindings` and revoke.
- Ingest: `POST /api/v1/experience/im/messages` requires `im.delegate`, resolves the
  binding, and creates the Turn as the bound Principal.
- Unmapped senders fail closed. `sender_display` is display-only.
- `obsion-im ingest --sender-id` submits that REST contract, then waits on the Run
  Event stream.
- Python and TypeScript SDKs wrap binding and ingest.

## Architecture decisions

Nicknames are not an authorization source. Channel names `feishu`, `dingtalk`, and
`wecom` may be stored as identity namespaces so a later real vendor adapter can reuse
the same table. Those names do not implement vendor APIs. ADR 0010 records the
decision.

## Validation

- `uv run pytest --no-cov` — 457 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase31_im_principal_mapping.py`, updated IM adapter tests, and SDK identity
  methods.
- Bound ingest: `Turn.created_by` equals the mapped User, not the bot.
- Unknown sender: HTTP 403 `unknown_im_sender`.
- Extra `display_name` fields: HTTP 422 `request_validation_failed`.
- Architecture AST: IM sources still do not import control-plane or vendor SDKs and
  submit `create_im_message` instead of `runtime.ask`.
- `@obsion/sdk` Node tests — 19 passed.
- `uv run ruff check .` — 0 findings.
- `uv run mypy` on control plane, SDKs, CLI, and IM adapter — 0 issues.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live Feishu/DingTalk/WeCom adapters require tenant applications and are not
  implemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
