# PHASE-68-REPORT — DingTalk / WeCom HTTP

## What was implemented

Phase 68 adds explicit DingTalk and WeCom Experience delivery transports. The
adapter remains an Experience client. It does not implement a second Harness.

- `DingTalkClient` authenticates with `/gettoken` and sends `/chat/send` to
  `https://oapi.dingtalk.com`. Redirects are disabled.
- `WeComClient` authenticates with `/cgi-bin/gettoken` and sends either
  `/cgi-bin/appchat/send` or `/cgi-bin/message/send` to
  `https://qyapi.weixin.qq.com`.
- `--deliver dingtalk-http` and `--deliver wecom-http` require matching channels
  and namespaced environment credentials. Generic `--deliver http` stays rejected.
- Existing `POST /api/v1/experience/im/runs/{id}/deliveries` authorizes
  `im.reply.deliver` for every vendor channel and records PENDING/SENT/FAILED.
- Workbench IM copy documents the three explicit transports. Secrets stay out of
  TOML.
- ADR 0047 records that DingTalk/WeCom HTTP are Experience delivery, not
  Capabilities.

## Architecture decisions

Agent code never receives vendor credentials. Policy, not prompt text, decides
whether a completed Run may be posted. WeCom AES decrypt remains fail-closed
because EncodingAESKey handling is a separate inbound concern.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 727 passed, 22 skipped, 1
  deselected, including DingTalk/WeCom HTTP client tests and
  `test_phase68_dingtalk_wecom_http.py`.
- `uv run obsion scan-secrets` — 0 findings.
- Architecture AST allows `httpx` only in `feishu.py`, `dingtalk.py`, and
  `wecom.py`, each pinned to one origin.

## Remaining risks

- Live DingTalk/WeCom tenant apps are operator-owned; this environment has Feishu
  credentials only.
- WeCom AES ciphertext decrypt remains unimplemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
