# PHASE-64-REPORT — Feishu knowledge docs

## What was implemented

Phase 64 adds Feishu cloud documents as an Organization Knowledge source.

- `FeishuDocsClient` authenticates with `tenant_access_token/internal` and
  fetches docx metadata plus `raw_content`. Wiki tokens resolve through
  `wiki/v2/spaces/get_node` and must yield `obj_type=docx`.
- Connector `obsion-feishu-docs` is HTTP, egress-pinned to
  `https://open.feishu.cn`. Capability `knowledge.ingest` is L2
  `IDEMPOTENT_WRITE` behind Policy `knowledge.write`.
- `POST /api/v1/knowledge/sources/feishu/documents` is the operator write path.
  Content reuses Parser → Chunk → ACL → Index. `source=feishu`.
- ACL is required unless `inherit_acl` maps Feishu permission members. The
  connector never invents `organization: true`.
- Workbench 企业知识 can submit a document token. Python and TypeScript SDKs
  expose the same contract.
- ADR 0043 records that this is Knowledge, not IM Experience.

## Architecture decisions

Agent code never receives Feishu credentials. Knowledge Agent remains L1
retrieval. Sheets and other wiki objects fail closed. Public ingress,
DingTalk/WeCom docs, and wiki space crawl are not this phase.

## Validation

- `uv run pytest --no-cov -k "not maven"`: 691 passed, 20 skipped, 1 deselected.
- TypeScript SDK: 18 passed after `npm run build --workspace @obsion/sdk`.
- `uv run obsion scan-secrets`: 0 findings.
- Official-shaped Feishu wiki/docx fixtures ingest into `source=feishu`, appear
  in ACL search, and are cited by a Harness KNOWLEDGE Run.
- Missing credentials, missing ACL, sheet wiki nodes, and missing scopes fail
  closed. Secrets stay out of TOML and source.

## Remaining risks

- Live ingest still requires docx/wiki scopes on the tenant application.
- Wiki space listing is not implemented.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
