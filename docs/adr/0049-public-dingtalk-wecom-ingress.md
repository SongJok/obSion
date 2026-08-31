# ADR 0049: Public IM ingress covers Feishu, DingTalk, and WeCom

- Status: Accepted
- Date: 2026-08-30
- Amends: ADR 0046

## Context

ADR 0046 allowed `--public` only for Feishu because DingTalk/WeCom HTTP and
WeCom AES were unfinished. Phases 68 and 69 closed those gaps. Operators now
need the same explicit TLS + Host allowlist pattern for all three vendor
namespaces without opening unsigned public ingest.

## Decision

`obsion-im serve --public` accepts `feishu`, `dingtalk`, and `wecom` when:

- bind host is non-loopback;
- TLS cert/key files and `OBSION_IM_PUBLIC_HOSTS` are set;
- channel security is present:
  - Feishu: `OBSION_FEISHU_ENCRYPT_KEY`
  - WeCom: `OBSION_WECOM_ENCODING_AES_KEY` and `OBSION_WECOM_TOKEN`
  - DingTalk: `OBSION_DINGTALK_APP_SECRET` (via credentials) or
    `OBSION_IM_WEBHOOK_SECRET`

`development` public binds fail closed. Generic `--deliver http` remains
rejected. Public ingress is Experience, not a Capability.

## Consequences

Tenant callback URLs can target Feishu, DingTalk, or WeCom through one App
Server Experience adapter. Live DNS and certificates remain operator-owned.
