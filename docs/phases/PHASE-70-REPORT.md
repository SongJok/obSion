# PHASE-70-REPORT — Public DingTalk / WeCom ingress

## What was implemented

Phase 70 extends explicit public TLS webhook hosting to DingTalk and WeCom.

- `--public` accepts `feishu`, `dingtalk`, and `wecom` with TLS + Host allowlist.
- WeCom public requires EncodingAESKey and Token.
- DingTalk public requires app secret or webhook secret.
- `development` public binds fail closed.
- Healthz reports `vendor_verification` alongside `feishu_verification`.
- ADR 0049 amends ADR 0046.

## Architecture decisions

Public ingress remains Experience, not a Capability. Channel security is
mandatory before a non-loopback bind is accepted.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 740 passed, 22 skipped, 1
  deselected.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live public URL and DNS still require operator hosting.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
