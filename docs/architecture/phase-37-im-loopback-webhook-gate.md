# Phase 37 IM loopback webhook review

## Review question

Can documented vendor IM callbacks be POSTed to a 127.0.0.1 listener, complete
url_verification without a token, fail closed on WeCom AES ciphertext, and still
refuse vendor HTTP POST and non-loopback binds?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `obsion-im serve --listen 127.0.0.1[:port]` binds loopback only.
- `GET /healthz` reports `delivery: local_outbox`.
- Feishu `url_verification` returns the challenge without `OBSION_TOKEN`.
- Message POST requires `OBSION_TOKEN` and control-plane principal mapping.
- WeCom AES ciphertext without EncodingAESKey fails closed and requires
  `OBSION_WECOM_ENCODING_AES_KEY` (Phase 69).
- `--listen 0.0.0.0` and `--deliver http` fail closed.
- Stdlib `http.server` is the listener. Vendor SDKs, HTTP clients, and vendor
  endpoint strings remain forbidden.

## Automated acceptance map

- `apps/im-adapter/tests/test_im_webhook.py` covers bind rejection, health,
  url_verification, and 401 without a token.
- `apps/im-adapter/tests/test_im_envelopes.py` rejects WeCom `Encrypt` ciphertext.
- `apps/im-adapter/tests/test_im_main.py` rejects `--listen 0.0.0.0:8787`.
- `apps/im-adapter/tests/test_im_architecture.py` forbids vendor HTTP clients.
- `services/control-plane/tests/test_phase37_im_loopback_webhook.py` covers ciphertext,
  bind, and HTTP delivery refusal.

## Human review checklist

- Confirm operators never expose this listener beyond loopback without a separate
  ingress and tenant application.
- Confirm vendor HTTP POST and WeCom AES decrypt remain absent.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
