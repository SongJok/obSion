# ADR 0044: Feishu wiki spaces sync as Knowledge

- Status: Accepted
- Date: 2026-08-30

## Context

goal.txt lists 飞书文档 and 内部知识库 in the Knowledge Pipeline. Phase 64
ingests one Feishu docx or wiki node. A knowledge base is a space of nodes.
Listing or ingesting that catalog through IM Experience, or inventing a space
tree when Feishu denies the call, would break Capability Fabric and Evidence.

Wiki spaces still have mixed object types. Sheets and bitables are not
documents. Treating them as successful ingest would fabricate Organization
Knowledge.

## Decision

`knowledge.sync` is an L2 HTTP Capability on `obsion-feishu-docs`. It lists
wiki spaces and walks nodes with a page, depth, and node budget. Each `docx`
node reuses `knowledge.ingest` and `KnowledgeService`. Non-docx nodes are
recorded as `skipped` with `feishu_docs_obj_type_unsupported`. Space-level
credential or OpenAPI failures fail closed. ACL remains explicit or inherited
from Feishu members and never becomes `organization: true` because the bot
can list a space.

Knowledge Agent remains L1 `knowledge.search`. Sync is an operator write path.
The Feishu docs client is not the IM adapter client.

## Consequences

A wiki SOP can be cited by a later Harness KNOWLEDGE Run after space sync.
Confluence, public IM ingress, and DingTalk/WeCom HTTP remain later phases.
