# Phase 67 public IM ingress review

## Review question

Can Feishu official events reach the IM adapter on a non-loopback bind without
exposing unsigned plaintext HTTP, other vendor channels, or an open Host?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Default `--listen` still binds `127.0.0.1` only.
- `--public` requires Feishu, Encrypt Key, TLS files, and `OBSION_IM_PUBLIC_HOSTS`.
- TLS is TLSv1.2+. Host mismatches return 403.
- DingTalk and WeCom public binds fail closed.
- Agents still do not receive Feishu credentials.

## Automated acceptance map

- `test_im_webhook.py` covers public bind parsing, ingress fail-closed, TLS
  health, and Host deny.
- `test_im_main.py` covers `--public` without TLS files.
- Architecture AST still forbids vendor SDKs and non-Feishu HTTP clients.

## Human review checklist

- Confirm the public Host allowlist matches the Feishu subscription URL.
- Confirm TLS certificates are operator-owned and not committed.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
