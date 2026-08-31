# Phase 62 vendor IM HTTP review

## Review question

Can a completed Feishu IM Run be delivered through a real Feishu OpenAPI client
after Policy authorization, while generic HTTP, DingTalk, WeCom, and public
webhooks remain fail-closed?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `--deliver feishu-http` requires `--channel feishu` plus
  `OBSION_FEISHU_APP_ID` / `OBSION_FEISHU_APP_SECRET`.
- `--deliver http`, HTTP(S) outbox paths, and non-loopback `--listen` fail closed.
- Config files still reject vendor secrets.
- Control-plane `im_deliveries` authorize `im.reply.deliver`, pin the answer
  fingerprint, and record SENT/FAILED receipts.
- The Feishu client pins `https://open.feishu.cn`, disables redirects, redacts
  credentials from errors, and does not import vendor SDKs.
- DingTalk/WeCom HTTP, WeCom AES decrypt, and public webhook hosting remain
  unimplemented.
- The adapter remains an Experience client of one App Server.

## Automated acceptance map

- `apps/im-adapter/tests/test_feishu.py` covers token cache, retries, redaction,
  and an opt-in live tenant-token check (`OBSION_FEISHU_LIVE=1`).
- `apps/im-adapter/tests/test_im_config.py` and `test_im_main.py` reject generic
  HTTP and other-vendor `feishu-http`.
- `apps/im-adapter/tests/test_im_bridge.py` authorizes and records live receipts.
- `apps/im-adapter/tests/test_im_architecture.py` allows `httpx` only in
  `feishu.py`.
- `services/control-plane/tests/test_phase62_feishu_http.py` covers idempotent
  receipts, retry after failure, non-IM Run refusal, tenant isolation, and
  Workbench copy.

## Human review checklist

- Confirm operators never commit Feishu secrets or write them into TOML.
- Confirm DingTalk/WeCom HTTP is still absent until those tenant apps exist.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
