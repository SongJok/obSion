# ADR 0076: Workbench interaction testing

- Status: accepted
- Date: 2026-09-01
- Phase: 97

## Context

The Web test stack (Phase 91) covered pure helpers and hooks, but no
test mounted a component and drove it the way an operator does. The
Phase 88 audit listed "broader Workbench interaction tests" as the
last open reliability item: the composer keyboard contract, the
claim-action provenance flow, the collaboration creation flow, and
the Automation and governance-console lifecycles were all exercised
only by hand, as was the governed Action lifecycle. Goal rule 11
(complete testing and optimization of all features) makes this a
release-blocking gap.

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

5. **Runtime inspector tabs follow the accessible tab contract.**
   The tablist owns six `tab` buttons and one associated `tabpanel`.
   Exactly one tab is selected and keyboard-focusable; ArrowLeft,
   ArrowRight, Home, and End both select and focus the destination.
   The existing component `tab` state stays the only state source.

6. **Governed Action modals are named dialogs.** Draft creation,
   approval, and preflight/rollback reason surfaces expose
   `role=dialog`, `aria-modal=true`, and stable heading association.
   This makes the active high-risk decision surface identifiable to
   assistive technology without changing any security decision.

7. **Studio comparison state is selection-scoped.** Agent, Skill, and
   Workflow selectors implement the accessible tab contract. Changing
   kind or immutable version clears the baseline and comparison so a
   version number from another registry object cannot cross the API
   boundary.

8. **Eval state is dataset-scoped and JSON is shape-checked.** Initial
   cases load once; dataset switches clear baseline, candidate, results,
   and comparison; the current candidate is not offered as its own
   baseline. Case documents and run bindings must be JSON objects, and
   every run-binding value must be a Run ID string before transport.

9. **A new Knowledge query invalidates old Evidence immediately.** The
   query is trimmed once, prior hits clear before transport, and a failed
   search cannot leave an older authorized result under the new query.
   Upload and vendor operations continue through their existing typed
   APIs with ACL metadata; tests never read connector credentials.

10. **Metric lineage is generation-scoped.** Definition selection,
    modal close, and every new lineage request invalidate older async
    responses. The definition/lineage selector follows the accessible
    tab contract, while the API remains the only source of lineage facts.

11. **Code search is normalized and generation-scoped.** Every search
    clears prior symbols, sends one trimmed term, distinguishes an empty
    authorized result from a failed transport, and ignores late results
    from older queries.

12. **Workspace asset paths and metadata are explicit.** Files starts
    without a fabricated filename and derives a normalized upload path
    from the chosen file unless the operator supplies one. File and
    Artifact uploads keep Workspace classification and lineage metadata;
    history, download, filter, preview, and refresh remain API projections.

13. **Workspace projection views render persisted facts only.** Reports,
    SQL, Evidence, and Timeline resolve details by persisted ID from their
    scoped collection. Dashboards accept only unique non-empty string
    Artifact references and clear panel errors on a new selection; they do
    not synthesize missing panels or execute SQL.

14. **Frozen-contract wheel validation is offline reproducible.** The
    PEP 517 backend is a locked dev dependency. The test uses the synced
    environment with `--no-build-isolation --offline`, builds a real wheel
    into a fresh directory, and still requires exact Event/Error resource
    equality. Network availability is not part of the contract.

## Consequences

- Four operator-critical surfaces now have executable interaction
  contracts: composer keyboard/stop/attachment behaviour, claim →
  task provenance with error mapping, collaboration task creation
  with all three actionable error branches, and Automation workflow
  version/trigger/schedule/retirement operations.
- Strict TypeScript checking covers mocks as well as production code;
  the Automation publish mock must preserve the real
  `{ workflow, version }` response envelope.
- Runtime details are operable without a pointer, and tab selection is
  conveyed to assistive technology instead of existing only as a CSS
  class.
- Existing high-risk governance operations now have executable UI
  contracts without weakening their server-side controls: IM identity
  uses stable sender IDs, connector discovery does not bind a
  Capability, and plugin health/scan/promotion results refresh from the
  control plane.
- Governed Action tests pin the V1 boundary at the UI transport edge:
  no production option, no preflight without an operator declaration,
  approval and rollback reasons preserved, and every cancellation sent
  through the Action API. Backend Policy and independent-approver
  enforcement remain the security authority.
- Studio now exposes keyboard-operable kinds and cannot leak stale
  comparison state across Agent/Skill/version boundaries.
- Eval avoids duplicate initial reads and cannot submit stale
  cross-dataset baselines, self-comparisons, JSON arrays, or non-string
  Run bindings.
- Knowledge cannot visually reattribute stale Evidence after a failed
  search, and normalized queries cross the API boundary once.
- Data cannot render a late lineage response for the wrong metric; its
  detail modes are keyboard-operable and source read-only status remains
  explicit.
- Code cannot visually reattribute stale symbols across queries, while
  repository ACL and static-index semantics remain backend-authoritative.
- Workspace Files and Artifacts preserve version/history, classification,
  lineage, and ID-based selection without silently naming every upload
  `notes.txt`.
- Reports, Dashboards, SQL, Evidence, and Timeline now have executable
  projection contracts that keep Artifact/Evidence/Event IDs and content
  authoritative, including Dashboard panel failure recovery.
- Contract distribution no longer flakes on PyPI access; build backend
  provenance and the resulting SBOM remain explicit and locked.
- A real UX defect (guidance erased by its own follow-up refresh) was
  found and fixed; the version-conflict recovery path operators hit
  under concurrent editing now keeps its instructions on screen.
- No backend change, no dependency change; the candidate contract,
  recorded evidence, and all six PENDING operator gates are
  untouched.
