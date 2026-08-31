# Phase 70 public DingTalk / WeCom ingress review

## Review question

Can DingTalk and WeCom use the same explicit public TLS webhook path as Feishu,
with channel-specific security, while unsigned public binds and development
channels still fail closed?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `--public` requires TLS files and `OBSION_IM_PUBLIC_HOSTS` for all vendors.
- Feishu still requires Encrypt Key.
- WeCom requires EncodingAESKey and Token.
- DingTalk requires app secret or webhook secret.
- Loopback remains the default. Host allowlist still applies.
- The adapter remains an Experience client of one App Server.

## Automated acceptance map

- `test_im_webhook.py` covers Feishu, DingTalk, and WeCom public resolution.
- `test_phase70_public_vendor_ingress.py` checks Workbench copy and fail-closed
  messages.
- Default listen still rejects non-loopback without `--public`.

## Human review checklist

- Confirm operators never commit TLS keys or vendor secrets.
- Confirm live public DNS remains operator-owned.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
