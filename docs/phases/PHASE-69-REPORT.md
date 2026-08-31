# PHASE-69-REPORT — WeCom AES decrypt

## What was implemented

Phase 69 adds WeCom EncodingAESKey decrypt to the Experience IM adapter.

- `decrypt_wecom_cipher` implements the documented AES-256-CBC package.
- `prepare_wecom_payload` verifies `msg_signature` when Token is set, decrypts
  `Encrypt`, and checks receive id against `OBSION_WECOM_CORP_ID`.
- Loopback webhook, `ingest --envelope`, and stdin serve paths reuse the same
  prepare step.
- Echostr decrypt returns `UrlVerification` without creating a Turn.
- Ciphertext without EncodingAESKey still fails closed.
- ADR 0048 records that WeCom AES is Experience inbound, not a Capability.

## Architecture decisions

AES keys never enter Agent context or TOML. Decrypt happens only in the IM
adapter before principal mapping. Public WeCom hosting remains unimplemented.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 738 passed, 22 skipped, 1
  deselected, including `test_wecom_aes.py` and `test_phase69_wecom_aes.py`.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live WeCom EncodingAESKey and Token are operator-owned.
- Public WeCom TLS ingress is not this phase.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
