# PHASE-65-REPORT — Feishu wiki spaces

## What was implemented

Phase 65 adds Feishu wiki space listing and sync as an Organization Knowledge
source.

- `FeishuDocsClient` lists `/wiki/v2/spaces` and walks `/wiki/v2/spaces/{id}/nodes`
  with page, depth, and node budgets. Numeric `space_id` values are accepted.
- Capability `knowledge.sync` is L2 `IDEMPOTENT_WRITE` behind Policy
  `knowledge.write` and bound to `obsion-feishu-docs`.
- `GET /api/v1/knowledge/sources/feishu/spaces`,
  `GET /api/v1/knowledge/sources/feishu/spaces/{space_id}/nodes`, and
  `POST /api/v1/knowledge/sources/feishu/spaces/{space_id}/sync` are the
  operator paths. Each `docx` node reuses `knowledge.ingest`.
- Non-docx nodes are skipped. Space-level Feishu failures fail closed.
- Workbench 企业知识 can submit a space id. Python and TypeScript SDKs expose
  the same contract.
- ADR 0044 records that space sync is Knowledge, not IM Experience.

## Architecture decisions

Agent code never receives Feishu credentials. Knowledge Agent remains L1
retrieval. Sheets and other wiki objects are not ingested. Per-document ingest
errors are isolated with savepoints so one empty document does not poison the
space transaction.

## Validation

- `uv run pytest --no-cov -k "not maven"`: 698 passed, 21 skipped, 1 deselected.
- TypeScript SDK: 23 passed after `npm run build --workspace @obsion/sdk`.
- `uv run obsion scan-secrets`: 0 findings.
- Official-shaped wiki pagination and child-node walk ingest `docx` nodes into
  `source=feishu`, skip sheets, appear in ACL search, and are cited by a
  Harness KNOWLEDGE Run.
- Missing credentials and invalid space ids fail closed. Secrets stay out of
  TOML and source.

## Remaining risks

- Live sync still requires wiki and docx scopes on the tenant application.
- Confluence and public IM ingress are not implemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
