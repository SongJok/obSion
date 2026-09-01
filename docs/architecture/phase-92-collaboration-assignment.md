# Phase 92 architecture review: Collaboration assignment and source-Run provenance

## Scope

Close the Alpha.1 gap audit's top P1 item: the collaboration ledger
recorded assignees and source Runs, but the Workbench could neither set
nor display them. The phase touches one backend view schema (additive),
the member listing endpoint, the Web collaboration view, and the
Workbench shell.

## Model fit

- **Workspace-scoped governance is preserved.** All data flows through
  the existing endpoints; the only backend change is that
  `WorkspaceMemberView` now carries `display_name` and `email` from the
  organization-scoped `User` rows a workspace member could already
  reference by id. No new authorization surface: `service.list_members`
  still performs the workspace access check before the join runs.
- **Optimistic concurrency is respected.** Task edits send
  `expected_version` plus only the changed fields; the control plane
  remains the sole arbiter of versions (`workspace_task_version_conflict`
  still refreshes and asks for confirmation).
- **Provenance is a persisted-key correlation, never inferred.** The
  source-Run link renders `source_run_id` exactly as stored; options are
  a convenience for selection, and a Run outside the option window still
  renders as `Run {id}`. Nothing fabricates a relationship the ledger
  does not hold.
- **One control plane, one loading path.** `openRunInspection` reuses
  the Workbench's `loadInspection` bundle; the Runtime inspector is
  unchanged except for completing the route-label map (ANALYTICS,
  OPERATION, SUPPORT) so no route renders as a raw enum.

## Boundaries held

- `source_run_id` stays create-only, matching the backend schema (no
  update path exists, and none was added).
- Assignee validation stays server-side
  (`workspace_task_assignee_invalid`); the selector is a UX aid, not an
  authorization check.
- No schema migration, no new settings, no new endpoint; the member
  join is two queries in the API layer.

## Verification

- Live API tests: readable member identity, assignee-must-be-member on
  create and reassign, explicit-null assignee clearing, cross-workspace
  source-Run rejection for tasks and decisions, same-workspace link
  acceptance.
- vitest: 15 behavior tests for payload building, member/Run labeling,
  option bounding, and datetime-local conversion.
- Static boundary suite pins the cross-file wiring (facade, view,
  Workbench callback, styles, bookkeeping).
