# PHASE-71-REPORT — DingTalk knowledge docs

## What was implemented

Phase 71 adds DingTalk cloud documents as a Knowledge Capability source.

- `DingTalkDocsClient` authenticates against `api.dingtalk.com` and fetches
  document metadata/content/members.
- `obsion-dingtalk-docs` connector, builtins seed, and Capability bindings for
  `knowledge.ingest` / `knowledge.sync`.
- REST routes under `/knowledge/sources/dingtalk/...`.
- Workbench Knowledge UI can ingest a DingTalk document id.
- Error catalog codes `dingtalk_docs_*` are registered and covered by the
  static error-producer manifest.
- ADR 0050 records that DingTalk docs are Knowledge, not IM Experience.

## Architecture decisions

Credentials stay in the connector executor. ACL is never invented as
organization-wide from bot visibility. IM adapter modules are not imported.
Egress is pinned to `https://api.dingtalk.com`.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 748 passed, 22 skipped, 1
  deselected.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live DingTalk Doc API shapes may vary by tenant product SKU; operators must
  validate against their app scopes.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
