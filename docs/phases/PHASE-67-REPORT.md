# PHASE-67-REPORT — Public IM ingress

## What was implemented

Phase 67 adds an explicit public Feishu webhook mode.

- `obsion-im serve --listen` remains loopback unless `--public` is set.
- `--public` requires `OBSION_FEISHU_ENCRYPT_KEY`, TLS cert/key files, and
  `OBSION_IM_PUBLIC_HOSTS`. The listener is HTTPS and Host-checked.
- DingTalk and WeCom public hosting fail closed.
- ADR 0046 amends ADR 0016. Healthz reports `exposure` and `tls`.

## Architecture decisions

Public ingress is Experience, not a Capability. Official Feishu signatures from
Phase 63 stay mandatory on the public path. Generic HTTP delivery is still
rejected.

## Validation

- `uv run pytest --no-cov -k "not maven"`: 712 passed, 22 skipped, 1 deselected.
- `uv run obsion scan-secrets`: 0 findings.
- Default listen still rejects `0.0.0.0`. `--public` without TLS, Encrypt Key,
  or Host allowlist fails closed. TLS healthz reports `exposure=public` only
  for an allowed Host.

## Remaining risks

- A live public URL and DNS still require operator hosting.
- WeCom AES decrypt and DingTalk/WeCom HTTP remain unimplemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
