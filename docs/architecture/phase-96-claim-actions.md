# Phase 96 architecture review: post-conclusion context actions

## Scope and positioning

The Harness investigation model ends in Critic-verified claims; the
collaboration model begins with tasks and decisions. Until this phase
the two models met only by manual re-entry. Post-conclusion context
actions close that loop inside the Runtime inspector: each claim on a
completed run can become a workspace task or a decision record with
its provenance intact.

## What changed

- **`claim-actions.ts`**: pure builders — title truncation with an
  ordinal prefix (`结论 C1：…`), evidence line labeling with a
  five-line cap and an explicit remainder note, the task payload
  (statement, provenance line, verification status), and the decision
  payload (bounded summary, rationale with cited evidence).
- **Claims tab**: per-claim 转为任务 / 记录决策 buttons, shown only
  when the inspected run is COMPLETED and pinned to a workspace
  context.
- **Claim action modal**: prefilled editable title and body, saving
  through the Phase 92 collaboration endpoints with
  `source_run_id = run.id`; success offers "在协作中查看", which closes
  the inspector and opens the collaboration view.
- **Workbench**: new `onOpenCollaboration` prop on the inspector; the
  action resets on run switches alongside the other detail selections.

## Boundary compliance

- The workspace is taken from `run.workspace_context.workspace_id`,
  never from ambient session state; the backend's
  `workspace_source_run_mismatch` validation remains the fail-closed
  enforcement and is mapped to an actionable message.
- No backend route, schema, or permission change; tasks and decisions
  flow through the existing collaboration service with optimistic
  concurrency and audit unchanged.
- Claims are rendered, never altered; the modal edits only the new
  record's payload.
- The release-candidate contract, recorded evidence, and the six
  PENDING operator gates are untouched.

## Testing

- 8 vitest cases: truncation and whitespace collapsing, ordinal
  titles, evidence line labeling with dangling-id skips and the cap,
  task payload provenance, decision rationale with confidence and
  cited evidence, and the remainder note.
- 6 control-plane tests: live acceptance of the exact payload shapes
  with same-workspace provenance, cross-workspace rejection, static Web
  wiring, and bookkeeping.
