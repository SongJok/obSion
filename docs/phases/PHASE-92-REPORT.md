# Phase 92 report: Collaboration assignment and source-Run provenance

## What was implemented

The gap audit's top P1 item is closed — the collaboration ledger's
assignment and provenance fields are now fully operable in the
Workbench:

- **Readable member identity (backend, additive)**: `WorkspaceMemberView`
  gains `display_name` and `email`; both member endpoints populate them
  by joining organization-scoped `User` rows after the existing
  workspace access check.
- **Member selector**: the task form (create and the new edit mode)
  offers every active workspace member by name, with "未指派" clearing
  the assignment via an explicit `assignee_id: null`.
- **Source-Run selector**: task and decision creation can attach the
  motivating Run, chosen from the workspace's threads' runs sorted
  newest-first and capped at 50 options.
- **Provenance display**: task cards show the assignee's name and a
  source-Run chip; the decision detail shows its source Run; persisted
  Runs outside the option window still render as `Run {id}`.
- **Cross-view link**: clicking a source Run fetches it and loads the
  full inspection bundle in the Runtime inspector via the Workbench's
  existing `loadInspection` path.
- **Error mapping**: invalid-assignee and cross-workspace-Run rejections
  surface actionable Chinese messages and refresh the selectors.
- **Folded-in minor item**: the Runtime inspector's route labels now
  cover ANALYTICS, OPERATION, and SUPPORT instead of falling back to
  raw enum names.

## Architecture decisions

ADR 0071 records the five decisions: readable identity on the existing
member view, bounded sorted selectors, changed-fields-only edits with
explicit-null clearing, backend-error-to-action mapping, and reusing
`loadInspection` for the provenance link.

## Migration

None. The view-schema change is additive; no database, settings, or
client contract migration is required. Rollback is reverting the phase
commits.

## Validation

- Live API tests in `test_phase92_collaboration_assignment.py`: member
  identity join, assignee validation on create/reassign, explicit-null
  clearing, cross-workspace source-Run rejection (task and decision),
  same-workspace acceptance.
- vitest: 60/60 web tests pass, including the 15 new
  `collaboration-display` behavior tests and the Phase 91-deferred
  hook/stream suites packaged with this phase.
- Web typecheck, lint, and production build pass.
- `make check` (pytest across the control plane, all node suites,
  eslint, tsc, alembic) and `make test-java` pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Native Claude/Gemini model adapters, Automation Web authoring depth,
Code Intelligence cross-language precision, post-conclusion context
actions, the operations analytics loop, full admin CRUD, and a
schema-driven chart renderer remain candidates.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase completes Web coverage of an
existing backend contract and does not advance promotion.
