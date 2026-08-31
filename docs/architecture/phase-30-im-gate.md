# Phase 30 Experience IM adapter review

## Review question

Can an IM entrance submit Workspace → Thread → Turn → Run through the existing App
Server and REST surfaces, reuse one Thread per conversation, avoid a second Harness,
and refuse vendor Feishu/DingTalk/WeCom implementations that would fake those APIs?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `obsion-im` lives in `apps/im-adapter` and depends on `obsion-sdk` plus `obsion-cli`.
- Only `channel=development` is implemented. Vendor channel names are rejected.
- One conversation id maps to one Thread titled `im:{channel}:{conversation_id}`.
- Inbound text is a Turn. Replies come from `answer.delta` / artifacts after the Run
  is terminal. Tokens never appear in replies.
- Architecture tests forbid control-plane, Harness, vendor SDK, and vendor endpoint
  strings.
- No second backend language. No FastAPI server inside the adapter.

## Automated acceptance map

- `apps/im-adapter/tests/test_im_architecture.py` forbids a second runtime and vendor
  clients.
- `apps/im-adapter/tests/test_im_config.py` rejects vendor channels and credential
  config.
- `apps/im-adapter/tests/test_im_bridge.py` covers App Server turn mutation and
  conversation Thread reuse.
- `services/control-plane/tests/test_phase30_experience_im.py` ingest two messages
  against the in-process control plane.

## Human review checklist

- Confirm operators will not point `obsion-im` at production IM tenant secrets.
- Confirm a later vendor adapter maps senders through the control-plane Principal
  resolver (`im_principal_bindings`), not through display names.

Phase 32 keeps this client boundary and interprets `feishu` / `dingtalk` / `wecom` as
inbound envelope namespaces. Vendor HTTP remains unimplemented.
