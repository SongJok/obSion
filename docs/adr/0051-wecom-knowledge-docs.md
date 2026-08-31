# ADR 0051: WeCom documents are a Knowledge Capability source

- Status: Accepted
- Date: 2026-08-30

## Context

Feishu, DingTalk, and Confluence already enter Organization Knowledge through
Capability Gateway connectors. WeCom documents were still Experience-only (IM).
Faking a WeCom Knowledge path through the IM adapter, or inventing
organization-wide ACL from bot membership or `enable_corp_internal`, would
violate the Knowledge ACL and Credential invariants.

## Decision

`obsion-wecom-docs` is a Knowledge Connector:

- egress pinned to `https://qyapi.weixin.qq.com`;
- credentials from `OBSION_WECOM_CORP_ID` / `OBSION_WECOM_CORP_SECRET` only;
- `knowledge.ingest` fetches a wedoc document and reuses Parser → Chunk → ACL →
  Index;
- `knowledge.sync` walks an operator-supplied WeDrive `space_id` and ingests
  document nodes only when a `docid` is present;
- ACL is explicit or inherited from `doc_get_auth` members and never defaults to
  `organization: true`;
- Agents never receive WeCom tokens; IM Experience does not ingest documents.

## Consequences

Operators can ingest WeCom docs beside Feishu, DingTalk, and Confluence. Live
tenant apps remain operator-owned. Unsupported WeDrive node types and files
without a resolvable `docid` fail closed and are skipped during sync.
