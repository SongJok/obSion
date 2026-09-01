# Phase 97 report: broader Workbench interaction tests

## What was implemented

The Phase 88 audit's last open reliability item — broader Workbench
interaction tests — is closed:

- **Interaction suite**: `tests/workbench-interactions.test.tsx`
  mounts real components in jsdom and drives them with `fireEvent`
  and accessible queries, mocking only the `@/lib/api` boundary via
  `importOriginal` spread (types and `ApiError` identity stay
  production-identical).
- **Composer coverage**: Enter submits, Shift+Enter and blank input
  do not, running turns the send button into stop, the context picker
  filters and adds, attachment chips remove.
- **Claim-action coverage**: verified claim → task with run-pinned
  workspace and `source_run_id`, success navigation into
  collaboration, actions hidden until COMPLETED, and the
  source-Run-mismatch actionable message.
- **Collaboration coverage**: task creation through the modal with an
  optional bounded source Run, and the assignee-invalid actionable
  message.

## Defect found and fixed

Writing the collaboration flow test surfaced a real UX defect: the
mutation handler surfaced its actionable message and *then*
refreshed, and the refresh clears notices on entry — so
version-conflict, assignee-invalid, and source-Run mismatch guidance
vanished before anyone could read it. The handler now refreshes
first and surfaces the message after; the version-conflict test pins
the ordering by asserting the guidance survives the follow-up reload.

## Architecture decisions

ADR 0076 records the four decisions: Testing Library interactions
with no new dependency, one mocked API boundary per suite, explicit
cleanup under `globals: false`, and treating a failing interaction
test as a defect report.

## Migration

None. No backend route, schema, permission, or API shape change.
Rollback is reverting the phase commits.

## Validation

- `apps/web/tests/workbench-interactions.test.tsx` — 10 interaction
  cases; full Web suite 105 passed.
- `services/control-plane/tests/test_phase97_workbench_interactions.py`
  — 4 tests: suite coverage markers, notice-survival pin,
  refresh-before-message ordering, and bookkeeping.
- `make check` and `make test-java` pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Code Intelligence cross-language precision (P3), the operations
analytics loop, and full admin CRUD remain candidates.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase extends test coverage and
does not advance promotion.
