# Phase 66 Confluence knowledge review

## Review question

Can a Confluence Cloud page enter the existing Knowledge pipeline through
Capability Gateway without becoming an IM path, inventing ACL, or following
pagination off the Cloud site origin?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Connector type `confluence` may only call `https://{site}.atlassian.net`.
- Credentials are environment-only. Configuration stores env names and the
  site host, not tokens.
- `knowledge.ingest` writes `source=confluence` through `KnowledgeService`.
- Storage HTML is parsed by the existing HTML parser. Draft pages fail closed
  on ingest and are skipped on space sync.
- ACL is explicit or inherited from Confluence restrictions. Inheritance never
  implies organization-wide access.
- Knowledge Agent stays L1 `knowledge.search`.
- Feishu and IM modules are not imported.

## Automated acceptance map

- `test_phase66_confluence_knowledge.py` covers site/ACL fail-closed, official
  page and cursor pagination shapes, off-origin next-link rejection, REST
  ingest, space sync skip of drafts, search, and a Harness citation Run.
- Workbench copy documents the Confluence source. AST forbids Harness, Feishu,
  and IM imports from `confluence.py`.

## Human review checklist

- Confirm the tenant Cloud site host and a scoped API token before live ingest.
- Confirm operators set an explicit ACL when page restrictions are empty.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
