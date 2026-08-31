# PHASE-36-REPORT — Vendor IM outbound envelopes

## What was implemented

Phase 36 renders Feishu, DingTalk, and WeCom replies as documented local-outbox
envelopes. The adapter remains an Experience client. It does not implement a second
Harness and does not call vendor HTTP APIs.

- `render_outbound` maps a completed Run answer onto the inbound identity namespace.
- Delivery is only `local_outbox`. `--deliver http` and HTTP(S) outbox paths fail
  closed.
- Optional `--outbox` / `OBSION_IM_OUTBOX` appends JSONL envelopes to a local file.
- Outbound `channel` is the inbound namespace (`feishu` / `dingtalk` / `wecom`), not
  the local transport name `development`.
- ADR 0015 records that outbound is envelope rendering, not HTTP.
- No schema migration; `im_principal_bindings` remains the identity source of truth.

## Architecture decisions

Identity stays on the control-plane binding table. The adapter never invents a second
principal path and never stores vendor app secrets in config files.

## Validation

- `uv run pytest --no-cov` — 494 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase36_vendor_im_outbound.py` and the IM adapter reply tests.
- `uv run obsion scan-secrets` — 0 findings.
- Architecture AST: IM sources do not import control-plane modules, HTTP clients, or
  vendor SDKs, and do not contain vendor endpoint strings.
- `--deliver http` returns the not-implemented error without connecting.
- Workbench at `http://localhost:3000` 治理控制台 IM 绑定 copy states that outbound
  renders vendor envelopes into the local outbox and that `--deliver http` is
  rejected. Composer still has one prompt and no Agent picker.

## Remaining risks

- Public webhook hosting, WeCom AES decrypt, and vendor HTTP POST require a real tenant
  application. Phase 37 adds a 127.0.0.1 listener for documented envelopes.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
