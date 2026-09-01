# ADR 0071: Collaboration assignment and source-Run provenance in the Workbench

## Status

Accepted (Phase 92, 0.92.0-dev)

## Context

The collaboration backend has been complete since its phase: tasks carry
`assignee_id` and `source_run_id`, decisions carry `source_run_id`, the
service enforces that an assignee is an active workspace member
(`workspace_task_assignee_invalid`) and that a source Run belongs to the
same workspace (`workspace_source_run_mismatch`), and clearing an
assignee is an explicit `assignee_id: null` distinguished from "field
not sent" via Pydantic `model_fields_set`. The Workbench used none of
it: the task form sent only title/description/priority/due_at, cards
rendered an opaque "已指派" badge, and neither record could link back to
the Run that motivated it. The Alpha.1 gap audit ranked this the top P1
gap — collaboration provenance existed in the ledger but was invisible
and unreachable in the product surface.

Constraints: the assignee selector needs readable member names, but
`WorkspaceMemberView` exposed only `user_id`, and the only
display-name endpoint (`/admin/users`) is admin-gated and therefore
unusable for ordinary workspace members. Source-Run selection needs a
bounded option list; a workspace accumulates runs without limit. The
cross-view "open this Run" link must reuse the existing inspection
loading path rather than duplicate it.

## Decision

1. **Member views carry readable identity.** `WorkspaceMemberView`
   gains `display_name` and `email`, populated by joining the already
   organization-scoped `User` rows in the API layer
   (`_member_views`). No new endpoint, no permission change: the data
   was already visible to any workspace member as `user_id`; the join
   only makes it human-readable.
2. **Selectors are plain `<select>` elements fed by bounded, sorted
   options.** Members come from `GET /workspaces/{id}/members`; source
   Run options come from the workspace's active threads' runs, sorted
   newest-first and capped at `MAX_SOURCE_RUN_OPTIONS = 50` so the
   selector never grows with history. A persisted `source_run_id` that
   fell out of the window still renders as `Run {id}` via
   `sourceRunLabel` — provenance never disappears.
3. **Edit uses the optimistic-concurrency path.** The task modal gains
   an edit mode whose payload is built by `taskUpdatePayload`: only
   changed fields are sent alongside `expected_version`, clearing the
   assignee is an explicit `null`, and no-op submissions are disabled
   client-side because the control plane rejects empty updates.
   `source_run_id` is create-only, matching the backend contract.
4. **Backend validation errors map to actionable messages.** The
   existing `mutate` wrapper now translates
   `workspace_task_assignee_invalid` and
   `workspace_source_run_mismatch` into instructions to refresh, and
   refreshes the member/Run lists so a stale selector self-heals.
5. **The provenance link reuses `loadInspection`.** The Workbench owns
   an `openRunInspection(runId)` callback — fetch the Run, load the
   full inspection bundle (events, steps, evidence, memories,
   conversation, claims, artifacts), switch to the assistant view, open
   the inspector. No second loading path, no URL scheme, no new API.

## Consequences

- The collaboration ledger's provenance is now reachable end-to-end:
  assign a task to a named member, attach the motivating Run at
  creation, and jump from a task or decision straight into that Run's
  Runtime inspector.
- `WorkspaceMemberView` is additive; existing consumers
  (`model_validate` callers were replaced, response-model consumers
  gain two fields) are unaffected.
- Behavior coverage for the payload semantics (explicit-null clear,
  changed-fields-only, bounded options, stale-Run fallback) runs in the
  vitest suite; live API tests pin the member identity join, assignee
  validation, explicit-null clearing, and cross-workspace source-Run
  rejection.
