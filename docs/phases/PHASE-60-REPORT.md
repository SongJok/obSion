# PHASE-60-REPORT — Workspace evidence

## What was implemented

Phase 60 lists persisted workspace Evidence rows.

- `GET /workspaces/{id}/evidence` joins Evidence → Run → Turn → Thread.
- Workbench adds a read-only Evidence rail that renders stored content.
- Greetings publish no evidence. Knowledge citations reuse the same rows as
  the Run inspector.

## Architecture decisions

A second evidence store or invented citations would be a demo. ADR 0039 keeps
the rail as a workspace join. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 650 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase60_workspace_evidence.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- DATA/CODE evidence appears only when those routes persist Evidence.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
