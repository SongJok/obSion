# PHASE-57-REPORT — Workspace reports

## What was implemented

Phase 57 publishes workspace REPORT artifacts from evidenced Harness answers.

- `_workspace_report_artifact` emits REPORT when citations, result artifacts, or
  incident fusion exist. Greetings and unknown answers do not. Existing
  engineering REPORT rows are not duplicated.
- Conversation TEXT remains the first run artifact. The report links
  `answer_artifact_id`.
- `GET /workspaces/{id}/reports` lists current REPORT rows. Workbench adds a
  read-only Reports rail.

## Architecture decisions

A dashboard of invented charts would be a demo. ADR 0036 keeps Reports as
published Harness artifacts. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 635 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase57_workspace_reports.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Data/Incident reports depend on those routes producing citations, result
  artifacts, or fusion.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
