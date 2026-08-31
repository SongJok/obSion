# Phase 56 Workspace files review

## Review question

Are workspace FILE uploads addressable by a governed path, versioned when that
path is reused, and kept out of SYSTEM context unless attached as Evidence?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `path` is optional. Empty or omitted paths stay in the Artifact center only.
- Normalized paths are absolute, segment-safe, and at most 512 characters.
- Reusing a current path increments `file_version` and sets `superseded_at`.
- `GET /workspaces/{id}/files` lists FILE rows with a path. History is opt-in.
- Workbench Files discloses that files do not automatically become SYSTEM text.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_phase56_workspace_files.py` covers path validation, versioning, tenant
  isolation, and AST/UI bans.

## Human review checklist

- Confirm operators do not paste workspace files into Prompt templates.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
