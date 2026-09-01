# ADR 0076: Workbench interaction testing

- Status: accepted
- Date: 2026-09-01
- Phase: 97

## Context

The Web test stack (Phase 91) covered pure helpers and hooks, but no
test mounted a component and drove it the way an operator does. The
Phase 88 audit listed "broader Workbench interaction tests" as the
last open reliability item: the composer keyboard contract, the
claim-action provenance flow, and the collaboration creation flow
were all exercised only by hand. Goal rule 11 (complete testing and
optimization of all features) makes this a release-blocking gap.

## Decisions

1. **Interaction tests through Testing Library, no new dependency.**
   The suite renders real components in jsdom and drives them with
   `fireEvent` plus accessible queries (roles, labels, display
   values). `@testing-library/user-event` is deliberately not added:
   fireEvent covers the interaction surface we own, and every added
   dependency must justify itself to the SBOM.

2. **One mocked API boundary per suite.** `@/lib/api` is mocked once
   with `importOriginal` spread, so types and the `ApiError` class
   stay identical to production while endpoint functions become
   spies. Assertions target the exact payloads crossing the boundary
   (workspace id, `source_run_id`, title) — the contract the backend
   enforces — rather than component internals.

3. **Explicit cleanup because globals are off.** The vitest config
   keeps `globals: false` (the TypeScript surface stays identical to
   app code), so the suite registers `afterEach(cleanup)` itself.
   Without it, renders accumulate across tests and accessible-name
   queries match stale duplicates — the suite pins this by living in
   one file that would fail loudly if cleanup were dropped.

4. **A failing interaction test is a defect report, not a test
   bug.** Writing the collaboration flow test surfaced that the
   mutation handler surfaced its actionable message and *then*
   refreshed, and the refresh clears notices on entry — so
   version-conflict, assignee-invalid, and source-Run mismatch
   guidance vanished before anyone could read it. The fix reorders
   refresh-then-message in the component; the test pins the ordering
   by asserting the guidance survives the follow-up reload. Tests
   that encode the buggy ordering were not an acceptable outcome.

## Consequences

- The three most operator-critical Workbench surfaces now have
  executable interaction contracts: composer keyboard/stop/attachment
  behaviour, claim → task provenance with error mapping, and
  collaboration task creation with all three actionable error
  branches.
- A real UX defect (guidance erased by its own follow-up refresh) was
  found and fixed; the version-conflict recovery path operators hit
  under concurrent editing now keeps its instructions on screen.
- No backend change, no dependency change; the candidate contract,
  recorded evidence, and all six PENDING operator gates are
  untouched.
