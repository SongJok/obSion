# PHASE-63-REPORT — Feishu official event signature

## What was implemented

Phase 63 verifies official Feishu webhook headers on the loopback listener.

- `official_feishu_signature` hashes timestamp, nonce, encrypt key, and the raw
  body exactly as Feishu documents.
- Encrypted `{encrypt}` events decrypt with AES-256-CBC after the signature
  matches. The documented `test key` vector decrypts to `hello world`.
- URL verification may remain unsigned. Other events require official headers
  once `OBSION_FEISHU_ENCRYPT_KEY` is set.
- Verification Token is optional and compared after decrypt.
- ADR 0042 records that the local JSON HMAC is development-only.

## Architecture decisions

This is Experience inbound security, not a Capability and not a second Harness.
Public bind, DingTalk HTTP, and WeCom AES remain fail-closed.

## Validation

- `uv run pytest --no-cov` on contract gates + IM adapter + Phase 62/63 tests:
  71 passed, 1 skipped. Official decrypt vector `test key` → `hello world`
  passed. `uv run obsion scan-secrets` reported 0 findings.
- Existing Feishu HTTP delivery tests remain green.
- Secrets stay out of TOML and source.

## Remaining risks

- Public webhook hosting still requires operator ingress or a tunnel.
- Live inbound still needs a Feishu Encrypt Key from the developer console.
- DingTalk/WeCom HTTP remain unimplemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
