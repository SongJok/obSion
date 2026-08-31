# Phase 68 DingTalk / WeCom HTTP review

## Review question

Can a completed DingTalk or WeCom IM Run be delivered through a real vendor
OpenAPI client after Policy authorization, while generic HTTP remains
fail-closed and Agents never receive vendor credentials?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `--deliver dingtalk-http` requires `--channel dingtalk` plus
  `OBSION_DINGTALK_APP_KEY` / `OBSION_DINGTALK_APP_SECRET`.
- `--deliver wecom-http` requires `--channel wecom` plus
  `OBSION_WECOM_CORP_ID` / `OBSION_WECOM_CORP_SECRET` / `OBSION_WECOM_AGENT_ID`.
- `--deliver http` and HTTP(S) outbox paths remain fail-closed.
- Config files still reject vendor secrets.
- Control-plane `im_deliveries` authorize `im.reply.deliver` for every vendor
  channel, pin the answer fingerprint, and record SENT/FAILED receipts.
- DingTalk client pins `https://oapi.dingtalk.com`. WeCom client pins
  `https://qyapi.weixin.qq.com`. Both disable redirects, redact credentials from
  errors, and do not import vendor SDKs.
- WeCom AES decrypt remains unimplemented.
- The adapter remains an Experience client of one App Server.

## Automated acceptance map

- `apps/im-adapter/tests/test_dingtalk.py` and `test_wecom.py` cover token cache,
  retries, redaction, appchat vs user message routing.
- `apps/im-adapter/tests/test_im_config.py` and `test_im_main.py` reject generic
  HTTP and mismatched channel/transport pairs.
- `apps/im-adapter/tests/test_im_architecture.py` allows `httpx` only in
  `feishu.py`, `dingtalk.py`, and `wecom.py`, each pinned to one origin.
- `services/control-plane/tests/test_phase68_dingtalk_wecom_http.py` covers
  Policy-authorized receipts and Workbench copy.

## Human review checklist

- Confirm operators never commit DingTalk/WeCom secrets or write them into TOML.
- Confirm WeCom AES ciphertext still fails closed.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
