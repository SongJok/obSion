# PHASE-56-REPORT — Workspace files

## What was implemented

Phase 56 adds a path-versioned Files ledger on top of existing Artifacts.

- `normalize_workspace_path` rejects relative, empty, and unsafe segments.
- FILE uploads may set `path`. The same current path increments `file_version`
  and supersedes the previous row. Alembic `e82d1b3c4a56`.
- `GET /workspaces/{id}/files` lists current path-bearing FILE artifacts.
  Untitled uploads stay in the Artifact center.
- Workbench adds a Files rail. Files are not Context Builder SYSTEM text.

## Architecture decisions

A second filesystem or object store would split the Artifact contract. ADR 0035
keeps one store and one Policy boundary. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 631 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase56_workspace_files.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Paths are ASCII segment-safe; Unicode names must be encoded by the client.
- Concurrent writes to the same path return `artifact_path_conflict`.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
