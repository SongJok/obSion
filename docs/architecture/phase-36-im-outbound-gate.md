# Phase 36 vendor IM outbound review

## Review question

Can a completed IM Run be rendered as a documented Feishu, DingTalk, or WeCom reply
envelope in the local outbox, reuse `im_principal_bindings`, and refuse HTTP delivery
without a second Harness or a fake vendor HTTP client?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Outbound identity namespace equals the inbound channel (`feishu` / `dingtalk` /
  `wecom` / `development`), not the local transport name.
- Vendor payloads are documented reply bodies. Delivery is only `local_outbox`.
- `--deliver http`, `OBSION_IM_DELIVER=http`, and HTTP(S) outbox paths fail closed.
- Optional `OBSION_IM_OUTBOX` / `--outbox` appends JSONL to a local file.
- The adapter must not import HTTP clients or vendor SDKs, and must not contain
  vendor endpoint strings.
- Principal mapping stays on `/api/v1/experience/im/messages` and
  `im_principal_bindings`. Nicknames cannot authorize.
- Live webhook hosting, WeCom AES decrypt, and vendor HTTP POST remain unimplemented.

## Automated acceptance map

- `apps/im-adapter/tests/test_im_replies.py` covers Feishu/DingTalk/WeCom envelopes,
  HTTP refusal, and JSONL persistence.
- `apps/im-adapter/tests/test_im_bridge.py` keeps outbound channel equal to inbound
  namespace and stores a local-outbox envelope.
- `apps/im-adapter/tests/test_im_main.py` rejects `--deliver http` and HTTP outbox URLs
  without connecting.
- `apps/im-adapter/tests/test_im_architecture.py` forbids Harness, HTTP clients, vendor
  SDKs, and vendor endpoints.
- `services/control-plane/tests/test_phase36_vendor_im_outbound.py` ingests a Feishu
  envelope as the bound User and renders a local-outbox reply for that `chat_id`.

## Human review checklist

- Confirm operators inspect local-outbox JSON before pointing a real tenant app at
  this adapter.
- Confirm vendor HTTP POST is still absent until a tenant application exists.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
