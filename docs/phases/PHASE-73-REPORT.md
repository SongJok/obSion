# PHASE-73-REPORT — Vendor Knowledge hardening

## What was implemented

Phase 73 hardens shared Vendor Knowledge connector contracts.

- `knowledge/connector_contract.py` provides sync budget, provenance, and REST
  rate-limit alignment with Capability Gateway.
- Feishu, DingTalk, WeCom, and Confluence clients consume `SyncBudgetTracker`
  and fail closed on budget exhaustion.
- Sync envelopes report `budget` / `provenance`; ingest writes unified
  provenance metadata.
- Search hits, Knowledge handler payloads, and citations carry provenance
  fields.
- Operator REST vendor ingest enforces Gateway-aligned rate limits.
- Connector examples and builtins declare `rate_limit_per_minute` and
  `knowledge_sync_budget`.
- ADR 0052 records the shared contract.

## Architecture decisions

Silent truncation is forbidden. REST cannot bypass Gateway rate-limit
semantics. Provenance fields are null when unknown, never invented.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 763 passed, 22 skipped, 1
  deselected.
- `uv run obsion scan-secrets` — 0 findings.

## Remaining risks

- Live vendor QPS and WeDrive/wiki shapes remain operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
