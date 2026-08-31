# ADR 0043: Feishu docs are a Knowledge source

- Status: Accepted
- Date: 2026-08-30

## Context

goal.txt lists 飞书文档 in the Knowledge Pipeline. Phase 62/63 made Feishu an
Experience channel. Treating chat delivery as document ingest, or letting an
Agent hold Feishu credentials, would collapse Capability Fabric into IM.

Feishu wiki nodes and docx documents are Organization Knowledge. They need
Document → Parser → Chunk → ACL → Index → Evidence. File upload already
implements that pipeline. Feishu is an external fetch and must pass through
the Capability Gateway.

## Decision

`obsion-feishu-docs` is an HTTP connector pinned to `https://open.feishu.cn`.
`knowledge.ingest` fetches one docx document (or a wiki node that resolves to
docx) and writes it through `KnowledgeService.ingest` with `source=feishu`.
Credentials come only from `OBSION_FEISHU_APP_ID` / `OBSION_FEISHU_APP_SECRET`.
ACL is operator-supplied or inherited from Feishu permission members. The
connector never sets `organization: true` because a bot can read a document.

Knowledge Agent remains L1 retrieval (`knowledge.search`). Operator ingest is
the Control Plane write path, matching file upload. Sheets, bitables, and
other wiki object types fail closed. IM adapter code is not reused.

## Consequences

A Feishu SOP can be cited by a later Harness KNOWLEDGE Run without a second
document store. Wiki space crawl, Confluence, and public ingress remain later
phases.
