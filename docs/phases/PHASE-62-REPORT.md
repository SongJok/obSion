# PHASE-62-REPORT — Vendor IM HTTP (Feishu)

## What was implemented

Phase 62 adds an explicit Feishu Experience delivery transport. The adapter
remains an Experience client. It does not implement a second Harness.

- `FeishuClient` authenticates with `tenant_access_token/internal` and sends
  `im/v1/messages` to `https://open.feishu.cn`. Redirects are disabled.
- `--deliver feishu-http` requires `--channel feishu` and namespaced environment
  credentials. Generic `--deliver http` stays rejected.
- `POST /api/v1/experience/im/runs/{id}/deliveries` authorizes `im.reply.deliver`,
  pins the completed answer fingerprint, and records PENDING/SENT/FAILED receipts.
- `im_deliveries` is the durable ledger. Feishu `uuid` reuses the delivery id.
- Workbench IM copy documents the explicit transport. Secrets stay out of TOML.
- ADR 0041 records that Feishu HTTP is Experience delivery, not a Capability.

## Architecture decisions

Agent code never receives Feishu credentials. Policy, not prompt text, decides
whether a completed Run may be posted. DingTalk, WeCom, public ingress, and
WeCom AES remain fail-closed because those tenant materials do not exist here.

## Validation

- Live Feishu tenant token: `FeishuClient.health()` and
  `test_feishu_live_tenant_token_when_operator_enables_it` against the operator
  tenant application returned `authenticated: true`. Secrets were not written
  into the repository.
- `uv run pytest --no-cov -k "not maven"` — 670 passed, 19 skipped, 1
  deselected, including `test_phase62_feishu_http.py` and IM adapter Feishu
  tests. A one-off Phase 44 IntegrityError did not reproduce on the green
  full-suite rerun.
- TypeScript SDK: 18 passed after `npm run build --workspace @obsion/sdk`.
- `uv run obsion scan-secrets` — 0 findings.
- Architecture AST still forbids Harness imports, vendor SDKs, DingTalk/WeCom
  endpoints, and `httpx` outside `feishu.py`.

## Remaining risks

- Public webhook hosting and official Feishu `X-Lark-Signature` header
  verification are not this phase.
- Sending a chat message still requires the bot to be in that `chat_id`.
- DingTalk/WeCom HTTP and WeCom AES remain unimplemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
