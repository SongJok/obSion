# Phase 73 Vendor Knowledge hardening review

## Review question

Do Feishu, DingTalk, WeCom, and Confluence Knowledge connectors share one
fail-closed sync budget, one provenance envelope, and the same rate-limit key
as Capability Gateway for Operator REST?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Shared `KnowledgeConnectorBudget` replaces silent vendor truncation.
- Sync results include `budget` and `provenance`.
- Ingest writes unified provenance onto `DocumentVersion.metadata_json`.
- Search hits and citations expose provenance fields without inventing values.
- REST vendor ingest enforces Gateway-aligned rate limits.
- Knowledge modules must not import IM adapter code.

## Automated acceptance map

- `test_phase73_vendor_knowledge_hardening.py` covers budget fail-closed,
  envelope fields, provenance search hits, REST rate-limit semantics, and
  shared-contract imports.

## Human review checklist

- Confirm operators configure `rate_limit_per_minute` and
  `knowledge_sync_budget` intentionally per tenant.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
