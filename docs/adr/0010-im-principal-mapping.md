# ADR 0010: IM senders map to provisioned Principals

- Status: Accepted
- Date: 2026-08-29

## Context

Phase 30 made `obsion-im` an Experience client of one App Server. Inbound text became
a Turn, but the adapter authenticated as the bot bearer. Chat products identify people
by stable vendor ids plus mutable nicknames. Treating a display name as the Run owner
would let anyone impersonate a colleague by changing a group nickname. Leaving Turns
owned by the bot would collapse every IM conversation onto one Principal and skip
per-user Policy, ACL, and audit.

Vendor Feishu/DingTalk/WeCom HTTP clients still cannot be implemented without tenant
applications. Identity mapping cannot wait on those adapters: the development channel
already creates Turns.

## Decision

The control plane owns IM identity. `(channel, sender_id)` maps to `users.id` inside
the organization. Channel values `development`, `feishu`, `dingtalk`, and `wecom` are
identity namespace tags, not implemented vendor clients. Nicknames, `display_name`,
and `sender_display` have zero authorization weight and are not binding keys.

Unmapped senders fail closed (`unknown_im_sender`). Binding and replacement require
`identity.write`. Ingest requires `im.delegate` on the bot Principal. After a successful
map, `WorkspaceService.create_turn` runs as the bound User, so `Turn.created_by` is that
User, not the bot. The IM adapter only calls `POST /api/v1/experience/im/messages` and
then waits on the durable Run; it does not create Turns itself and does not import
Harness or the database.

## Consequences

Web, CLI, IDE, and IM share one Principal resolver. Operators bind senders before
ingest. Phase 32 reuses this table for vendor inbound envelopes. Phase 36 renders
local-outbox vendor replies using the same bindings. Feishu/DingTalk/WeCom HTTP POST
remains unimplemented until a real tenant app exists.
