# Phase 69 WeCom AES decrypt review

## Review question

Can WeCom `Encrypt` callbacks be decrypted with EncodingAESKey after signature
verification, while ciphertext without the key still fails closed and Agents
never receive AES material?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `OBSION_WECOM_ENCODING_AES_KEY` must be a 43-character EncodingAESKey.
- Optional `OBSION_WECOM_TOKEN` verifies `msg_signature`.
- Optional `OBSION_WECOM_CORP_ID` pins the decrypted receive id.
- Ciphertext without EncodingAESKey fails closed.
- Decrypted XML reuses existing WeCom envelope parsing.
- Decrypted echostr becomes URL verification and does not create a Turn.
- Secrets stay out of TOML. The adapter remains an Experience client.

## Automated acceptance map

- `apps/im-adapter/tests/test_wecom_aes.py` covers round-trip encrypt/decrypt,
  signature failure, receive-id mismatch, echostr verification, and XML webhook
  parsing.
- Existing envelope and webhook tests still fail closed without EncodingAESKey.
- Architecture AST still forbids Harness imports and vendor SDKs.

## Human review checklist

- Confirm operators never commit EncodingAESKey or Token into the repository.
- Confirm public WeCom ingress remains unimplemented.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
