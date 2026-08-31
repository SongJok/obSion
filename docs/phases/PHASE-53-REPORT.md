# PHASE-53-REPORT — Workspace context

## What was implemented

Phase 53 adds Workspace Context as a first-class Context Builder layer.

- `snapshot_workspace` pins redacted Workspace identity and description on
  `runs.workspace_context` at Turn creation. Alembic `d72b0a5e2f34`.
- Context Builder emits AGENT `workspace-identity` and UNTRUSTED `workspace-description`.
- Replay copies the pin. Inspector shows the workspace name and states that the
  description is not a SYSTEM instruction. Metric `obsion.workspace.context`.

## Architecture decisions

Live Workspace re-read would silently change historical Runs. Putting description
into SYSTEM would be injection. ADR 0032 pins a snapshot and splits trust.
Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 621 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase53_workspace_context.py`.

## Remaining risks

- Workspace membership and Policy still authorize capabilities; the pin is context
  only.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
