# PHASE-61-REPORT — Workspace timeline

## What was implemented

Phase 61 lists persisted workspace Run Events.

- `EventStore.list_workspace` joins Event → Run → Turn → Thread.
- `GET /workspaces/{id}/timeline` returns those rows newest first.
- Workbench adds a read-only Timeline rail. It does not invent Harness steps.

## Architecture decisions

A fabricated Observe→Respond animation would be a demo. ADR 0040 keeps the
rail as a PostgreSQL event join. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 653 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase61_workspace_timeline.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Timeline is capped at 500 events and is not a live SSE workspace stream.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
