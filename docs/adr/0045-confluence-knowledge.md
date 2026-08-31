# ADR 0045: Confluence Cloud is a Knowledge source

- Status: Accepted
- Date: 2026-08-30

## Context

goal.txt lists Confluence in the Knowledge Pipeline beside 飞书文档. Phase 64/65
made Feishu a Capability source. Treating Confluence as IM Experience, or
allowing an arbitrary `base_url`, would collapse Capability Fabric into SSRF
and a second document store.

Confluence Cloud pages are Organization Knowledge. They need Document → Parser
→ Chunk → ACL → Index → Evidence. Storage HTML already has a parser. The
vendor fetch must pass through the Capability Gateway.

## Decision

`obsion-confluence` is an HTTP connector pinned to one Cloud host
(`*.atlassian.net`). Credentials come only from `OBSION_CONFLUENCE_EMAIL` and
`OBSION_CONFLUENCE_API_TOKEN`. `knowledge.ingest` fetches one current page.
`knowledge.sync` lists a space and skips non-current pages. ACL is operator
supplied or inherited from Confluence restrictions. The connector never sets
`organization: true` because a bot can read a page.

Pagination `_links.next` may not leave the site origin. Knowledge Agent remains
L1 `knowledge.search`. Server/Data Center hosts are out of scope and fail
closed. Feishu and IM clients are not reused.

## Consequences

A Confluence SOP can be cited by a later Harness KNOWLEDGE Run. Public IM
ingress and DingTalk/WeCom HTTP remain later phases.
