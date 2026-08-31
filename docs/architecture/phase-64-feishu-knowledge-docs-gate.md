# Phase 64 Feishu knowledge docs review

## Review question

Can a Feishu cloud document enter the existing Knowledge pipeline through
Capability Gateway without becoming an IM Experience path, inventing ACL, or
giving Agents Feishu credentials?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Connector type `feishu-docs` may only call `https://open.feishu.cn`.
- Credentials are environment-only. Configuration stores env names, not values.
- `knowledge.ingest` writes `source=feishu` through `KnowledgeService`.
- Wiki nodes must resolve to `docx`. Other object types fail closed.
- ACL is explicit or inherited from Feishu members. Inheritance never implies
  organization-wide access.
- Knowledge Agent stays L1 `knowledge.search`. Ingest is `knowledge.write`.
- IM adapter modules are not imported.

## Automated acceptance map

- `test_phase64_feishu_knowledge.py` covers token/ACL fail-closed, wiki resolve,
  missing-scope denial, REST ingest, search, and a Harness citation Run.
- Workbench copy documents the Feishu source. AST forbids Harness and IM imports
  from `feishu_docs.py`.

## Human review checklist

- Confirm the tenant application has `docx:document:readonly` before live ingest.
- Confirm operators set an explicit ACL when Feishu member inheritance is
  unavailable.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
