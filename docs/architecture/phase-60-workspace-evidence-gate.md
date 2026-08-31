# Phase 60 Workspace evidence review

## Review question

Does the workspace expose persisted Evidence rows without inventing citations
or a second evidence store?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Conversation greetings persist no Evidence and list none on the workspace.
- Knowledge citations appear on `GET /workspaces/{id}/evidence` with the same
  ids and fingerprints as `GET /runs/{id}/evidence`.
- Other workspaces and tenants do not see those rows.
- Workbench discloses that the rail does not fabricate evidence.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_phase60_workspace_evidence.py` covers greeting exclusion, knowledge
  publication, workspace isolation, tenant isolation, and UI/AST bans.

## Human review checklist

- Confirm operators do not treat this rail as Prompt or Skill text.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
