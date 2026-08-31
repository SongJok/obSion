# Phase 65 Feishu wiki spaces review

## Review question

Can a Feishu wiki space be listed and synced into the existing Knowledge
pipeline through Capability Gateway without inventing the catalog, ingesting
non-docx objects, or giving Agents Feishu credentials?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `knowledge.sync` walks wiki nodes with page, depth, and node budgets.
- Only `obj_type=docx` nodes enter `KnowledgeService.ingest`.
- Non-docx nodes are skipped with `feishu_docs_obj_type_unsupported`.
- Space-level credential and OpenAPI failures fail closed.
- ACL is explicit or inherited. Inheritance never implies organization access.
- Knowledge Agent stays L1 `knowledge.search`. Sync is `knowledge.write`.
- IM adapter modules are not imported.

## Automated acceptance map

- `test_phase65_feishu_wiki_spaces.py` covers space id validation, pagination,
  child-node walk, sheet skip, REST sync, search, and a Harness citation Run.
- Workbench copy documents wiki space sync. AST forbids Harness and IM imports
  from `feishu_docs.py`.

## Human review checklist

- Confirm the tenant application has wiki space read scopes before live sync.
- Confirm operators set an explicit ACL when Feishu member inheritance is
  unavailable.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
