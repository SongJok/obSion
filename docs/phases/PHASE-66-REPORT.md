# PHASE-66-REPORT — Confluence knowledge

## What was implemented

Phase 66 adds Confluence Cloud pages as an Organization Knowledge source.

- `ConfluenceClient` uses Cloud REST v2 (`/wiki/api/v2/pages`, spaces, and
  space pages) plus the documented v1 restriction API. Pagination cannot leave
  `https://{site}.atlassian.net`.
- Connector `obsion-confluence` is HTTP and egress-pinned to one Cloud host.
  `knowledge.ingest` and `knowledge.sync` are L2 `IDEMPOTENT_WRITE` behind
  Policy `knowledge.write`.
- `POST /api/v1/knowledge/sources/confluence/pages` and
  `POST /api/v1/knowledge/sources/confluence/spaces/{space_id}/sync` write
  `source=confluence` through Parser → Chunk → ACL → Index.
- ACL is required unless `inherit_acl` maps Confluence restrictions. The
  connector never invents `organization: true`.
- Workbench 企业知识 can submit a page id. Python and TypeScript SDKs expose
  the same contract.
- ADR 0045 records that this is Knowledge, not IM Experience.

## Architecture decisions

Agent code never receives Confluence credentials. Knowledge Agent remains L1
retrieval. Only current pages are ingested. Server/Data Center hosts fail
closed. Feishu docs and IM adapter clients are not reused.

## Validation

- `uv run pytest --no-cov -k "not maven"`: 709 passed, 22 skipped, 1 deselected.
- TypeScript SDK: 24 passed after `npm run build --workspace @obsion/sdk`.
- `uv run obsion scan-secrets`: 0 findings.
- Official-shaped Confluence page and cursor pagination ingest into
  `source=confluence`, appear in ACL search, and are cited by a Harness
  KNOWLEDGE Run. Draft pages are skipped on space sync. Off-origin `next`
  links fail closed.
- Missing credentials, missing ACL, and non-Cloud hosts fail closed. Secrets
  stay out of TOML and source.

## Remaining risks

- Live ingest requires a Cloud site host, email, and API token.
- Public IM ingress and DingTalk/WeCom HTTP are not implemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
