# ADR 0046: Public IM ingress is explicit TLS Feishu only

- Status: Accepted
- Date: 2026-08-30

## Context

ADR 0016 made IM webhooks loopback-only so a missing tenant application could
not become a public unauthenticated ingest surface. Operators still need a
vendor callback URL. Opening `0.0.0.0` without TLS, official Feishu signatures,
or a Host allowlist would recreate that surface.

DingTalk and WeCom public callbacks require the channel security materials
introduced in Phases 68–69. See ADR 0049.

## Decision

`obsion-im serve --listen` remains loopback by default. `--public` is an
explicit Experience exception that requires all of:

- bind host other than `127.0.0.1`
- `--channel feishu`
- `OBSION_FEISHU_ENCRYPT_KEY` so official `X-Lark-Signature` and AES events
  are enforced
- TLS files `OBSION_IM_TLS_CERT` and `OBSION_IM_TLS_KEY`
- `OBSION_IM_PUBLIC_HOSTS` Host allowlist

Missing any requirement fails closed. Public ingress is not a Capability and
does not give Agents Feishu credentials.

## Consequences

A tenant can point Feishu event subscriptions at an operator-owned HTTPS
endpoint. DingTalk/WeCom public hosting remains later. Generic `--deliver http`
remains rejected.
