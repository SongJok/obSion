# PHASE-37-REPORT — IM loopback webhook

## What was implemented

Phase 37 adds a loopback webhook to `obsion-im` so documented Feishu, DingTalk, and
WeCom callbacks can be POSTed to `127.0.0.1`. The adapter remains an Experience
client. It does not implement a second Harness and does not call vendor HTTP APIs.

- `obsion-im serve --listen 127.0.0.1[:port]` binds loopback only.
- Feishu URL verification returns the challenge without a token.
- Message POST still requires `OBSION_TOKEN` and `im_principal_bindings`.
- WeCom AES ciphertext fails closed. `--deliver http` remains rejected.
- ADR 0016 records that webhook hosting is loopback-only.
- No schema migration.

## Architecture decisions

Stdin `serve` remains. The listener uses stdlib `http.server` and must not import
HTTP clients or vendor SDKs. Public ingress and vendor HTTP POST stay operator-owned.

## Validation

- `uv run pytest --no-cov` — 501 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase37_im_loopback_webhook.py` and the IM adapter webhook tests.
- `uv run obsion scan-secrets` — 0 findings.
- Architecture AST still forbids vendor endpoints and HTTP clients.
- Workbench at `http://localhost:3000` 治理控制台 IM 绑定 copy mentions
  `obsion-im serve --listen 127.0.0.1:8787` and that `--deliver http` is rejected.
  Composer still has one prompt and no Agent picker.

## Remaining risks

- Public webhook hosting, WeCom AES decrypt, and vendor HTTP POST require a real
  tenant application and are not implemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
