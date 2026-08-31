# PHASE-59-REPORT — Workspace SQL

## What was implemented

Phase 59 lists published workspace SQL artifacts.

- `GET /workspaces/{id}/sql` returns current `ArtifactKind.SQL` rows.
- Workbench adds a read-only SQL rail. It does not execute queries or invent
  SELECT text.
- Data Runs already publish validated SQL; greetings and knowledge do not.

## Architecture decisions

A SQL console against the warehouse would be a demo. ADR 0038 keeps SQL as a
ledger of published artifacts. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 646 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase59_workspace_sql.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Live warehouse execution remains the read-only SQL proxy with a replica DSN.
  SQLite control-plane tests cannot run that path.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
