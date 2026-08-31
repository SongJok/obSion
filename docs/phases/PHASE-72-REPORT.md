# PHASE-72-REPORT — WeCom knowledge docs

## What was implemented

Phase 72 adds WeCom cloud documents as a Knowledge Capability source.

- `WeComDocsClient` authenticates against `qyapi.weixin.qq.com` and fetches
  wedoc metadata/content/auth plus WeDrive space nodes.
- `obsion-wecom-docs` connector, builtins seed, and Capability bindings for
  `knowledge.ingest` / `knowledge.sync`.
- REST routes under `/knowledge/sources/wecom/...`.
- Workbench Knowledge UI can ingest a WeCom document id.
- Error catalog codes `wecom_docs_*` are registered and covered by the static
  error-producer manifest.
- ADR 0051 records that WeCom docs are Knowledge, not IM Experience.

## Architecture decisions

Credentials stay in the connector executor. ACL is never invented as
organization-wide from bot visibility or corp-internal flags. IM adapter
modules are not imported. Egress is pinned to `https://qyapi.weixin.qq.com`.
WeDrive sync requires an operator-supplied `space_id`.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 756 passed, 22 skipped, 1
  deselected.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live WeCom Doc/WeDrive API shapes and app scopes remain operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
