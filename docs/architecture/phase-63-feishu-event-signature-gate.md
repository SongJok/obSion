# Phase 63 Feishu event signature review

## Review question

Can a loopback webhook verify official Feishu `X-Lark-Signature` headers and
decrypt documented AES-256-CBC events without becoming a second Harness or
opening a public bind?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Official signature is SHA-256 over timestamp, nonce, encrypt key, and raw body.
- `OBSION_FEISHU_ENCRYPT_KEY` / `OBSION_FEISHU_VERIFICATION_TOKEN` are
  environment-only.
- Encrypted `{encrypt}` bodies decrypt after a valid official signature.
- URL verification may omit official headers.
- When the encrypt key is set, non-verification events without official headers
  fail closed.
- Official headers without an encrypt key fail closed.
- Webhook bind remains `127.0.0.1`. WeCom AES remains unimplemented.

## Automated acceptance map

- `apps/im-adapter/tests/test_feishu_events.py` covers the documented decrypt
  vector, signature accept/reject, encrypted events, and token mismatch.
- `apps/im-adapter/tests/test_im_config.py` hides encrypt-key material from
  `repr`.
- `services/control-plane/tests/test_phase63_feishu_event_signature.py` covers
  Workbench copy and adapter AST.

## Human review checklist

- Confirm operators set Encrypt Key in the Feishu console before pointing a
  live event subscription at a tunnel.
- Public ingress remains operator-owned.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
