# ADR 0050: DingTalk documents are a Knowledge Capability source

- Status: Accepted
- Date: 2026-08-30

## Context

Feishu and Confluence already enter Organization Knowledge through Capability
Gateway connectors. DingTalk documents were still Experience-only (IM). Faking
a DingTalk Knowledge path through the IM adapter, or inventing organization-wide
ACL from bot membership, would violate the Knowledge ACL and Credential
invariants.

## Decision

`obsion-dingtalk-docs` is a Knowledge Connector:

- egress pinned to `https://api.dingtalk.com`;
- credentials from `OBSION_DINGTALK_APP_KEY` / `OBSION_DINGTALK_APP_SECRET` only;
- `knowledge.ingest` fetches a document and reuses Parser → Chunk → ACL → Index;
- `knowledge.sync` walks a workspace and ingests document nodes only;
- ACL is explicit or inherited from DingTalk members and never defaults to
  `organization: true`;
- Agents never receive DingTalk tokens; IM Experience does not ingest documents.

## Consequences

Operators can ingest DingTalk docs beside Feishu and Confluence. Live tenant
apps remain operator-owned. Unsupported node types fail closed and are skipped
during sync.
