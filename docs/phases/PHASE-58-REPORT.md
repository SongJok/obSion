# PHASE-58-REPORT — Workspace dashboards

## What was implemented

Phase 58 publishes workspace DASHBOARD artifacts that compose real CHART, TABLE,
and SQL rows from a Data Run.

- `_workspace_dashboard_artifact` emits DASHBOARD after result artifacts flush
  when a CHART exists. Greetings and knowledge answers do not. Existing
  DASHBOARD rows are not duplicated.
- Dashboard `inline_content` stores panel references only. It does not copy
  Vega encodings or invent `data.values`.
- `GET /workspaces/{id}/dashboards` lists current DASHBOARD rows. Workbench adds
  a read-only Dashboards rail that renders the referenced artifacts.

## Architecture decisions

A dashboard of invented series would be a demo. ADR 0037 keeps Dashboards as
references to published Harness artifacts. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 641 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase58_workspace_dashboards.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Data dashboards depend on the Data route producing a CHART from cited rows.
  SQLite control-plane tests cannot execute warehouse SQL, so publication is
  covered by the helper contract plus HTTP exclusion tests.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
