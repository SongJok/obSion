# ADR 0009: Experience IM adapter is an App Server client

- Status: Accepted (vendor channel rejection superseded in part by ADR 0011)
- Date: 2026-08-29

## Context

Obsion Experience includes Web, IDE, CLI, API, and IM adapters. After Phase 29 the
Workbench, CLI, and VS Code extension already terminate at one App Server and one
Harness. Chat products (Feishu, DingTalk, WeCom) are additional entrances, not
additional runtimes. Implementing a vendor bot that owns Observe → Understand →
Plan → Execute → Verify → Reflect → Respond would violate the runtime invariant.
Pretending to speak Feishu/DingTalk/WeCom HTTP APIs without tenant credentials would
also violate the rule against faking external integrations.

## Decision

`apps/im-adapter` (`obsion-im`) is a first-class Experience client. It depends on
`obsion-sdk` and `obsion-cli` (shared `ExperienceRuntime`) only. One IM conversation
maps to one Thread in an `IM` workspace. Inbound text becomes a Turn; outbound text
is reconstructed from the durable Run Event stream. The only implemented channel is
`development`. Vendor channel names are rejected with an explicit not-implemented
error. Config files follow the CLI credential ban; `OBSION_TOKEN` supplies the
bearer. The adapter never imports Harness, connectors, or vendor IM SDKs.

Vendor adapters, when an operator later supplies real apps, must implement the same
`ImChannel` contract and must resolve the sender to a control-plane Principal. Chat
nicknames are not an authorization source.

## Consequences

Web, CLI, IDE, and IM share one Principal, one Event Store, and one Policy path.
Development ingest/serve is enough to prove the client boundary without a second
HTTP control plane or a fake vendor client. Phase 31 records IM sender mapping in
the control plane. Chat nicknames are not an authorization source. Phase 32 translates
documented vendor callback envelopes into that ingest contract without calling vendor
HTTP APIs. Phase 36 renders vendor-shaped local-outbox replies; HTTP POST remains
unimplemented until a real tenant application exists.
