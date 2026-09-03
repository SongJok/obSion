# Phase 97 architecture review: broader Workbench interaction tests

## Scope and positioning

Goal rule 11 requires complete testing of all features. Until this
phase the Web suite covered pure helpers, hooks, and parsers, but no
test mounted a component and drove it the way an operator does — the
Phase 88 audit's last open reliability item. Phase 97 closes it with
interaction suites over nineteen operator-critical surfaces, and fixes
the defect the suites discovered.

## What changed

- **`tests/workbench-interactions.test.tsx`** (new, 12 cases):
  - *Composer*: Enter submits, Shift+Enter and blank input do not, the
    send button becomes a stop action while running, the context
    picker filters and adds a readable artifact, and attachment chips
    remove through their buttons.
  - *RuntimeInspector claim actions*: a verified claim on a COMPLETED
    run becomes a task whose payload carries the run-pinned workspace
    and `source_run_id`; the success path offers "在协作中查看";
    actions stay hidden while the run is not COMPLETED; a
    `workspace_source_run_mismatch` rejection maps to the actionable
    message.
  - *CollaborationView*: the create-task modal submits the composed
    payload (title, optional bounded source Run); assignee-invalid
    and version-conflict rejections map to their actionable messages.
  - *RuntimeInspector*: the six tabs support the WAI-ARIA
    tablist/tab/tabpanel relationship and roving keyboard focus with
    ArrowLeft, ArrowRight, Home, and End. Interactions traverse
    Context, Evidence, Memory, Claims, and Artifacts, including detail
    drawers and Claim-to-Evidence navigation.
- **`tests/automation-interactions.test.tsx`** (new, 7 cases):
  - *AutomationView*: overview/detail loading, older-version
    publication, validated manual triggers, malformed JSON rejection,
    schedule creation without an accidental version pin, guarded
    retirement, and immutable version derivation.
  - The publish mock returns the production contract
    `{ workflow, version }`; TypeScript strict checking therefore
    catches test/transport drift even when a runtime mock would pass.
- **`tests/admin-interactions.test.tsx`** (new, 5 cases):
  - *AdminView*: independent failure of one of the 22 governance
    projections, stable-sender IM binding and revocation, Connector SDK
    discovery without Capability auto-binding, and health/scan/promotion
    mutations with projection refresh.
  - The suite mocks only the typed Admin API boundary; it never loads a
    credential and does not weaken backend Policy, approval, scan, or
    audit enforcement.
- **`tests/actions-interactions.test.tsx`** (new, 5 cases):
  - *ActionsView*: development PR draft composition with an idempotency
    key, no production option, operator-authored preflight, independent
    approval reasoning, bounded rollback reasoning, and governed
    cancellation.
  - The suite stops at the typed Action API boundary. The backend
    remains authoritative for Policy, self-approval denial, immutable
    plan checksums, execution, idempotency, and Audit/Event persistence.
- **`tests/studio-interactions.test.tsx`** (new, 6 cases):
  - *StudioView*: roving accessible kind tabs, validation-only Workflow,
    immutable Skill publication, explicit promotion and rollback, and
    comparison that remains `traffic_split=false`.
  - Switching kind or version clears the comparison baseline and result,
    preventing a stale version from another registry object entering the
    request.
- **`tests/eval-interactions.test.tsx`** (new, 6 cases):
  - *EvalView*: single initial case fetch, dataset creation and scoping,
    case JSON validation, fully pinned Evaluation Run input, strict
    run-binding objects, and distinct same-dataset comparison.
  - Dataset changes clear candidate, baseline, results, and comparison;
    the active candidate is excluded from baseline choices.
- **`tests/knowledge-interactions.test.tsx`** (new, 5 cases):
  - *KnowledgeView*: normalized authorized search, recorded provenance,
    stale-result removal on failure, ACL-bearing local upload, four
    vendor-specific ingestion routes, and Feishu space synchronization.
  - Results clear before every new request, preventing old Evidence from
    appearing under a new failed query.
- **`tests/data-interactions.test.tsx`** (new, 5 cases):
  - *DataView*: verified metric search, definition dialog, accessible
    detail tabs, read-only lineage, explicit failure, and rapid metric
    switching.
  - A generation counter protects lineage state; a late response for a
    closed or previous metric cannot overwrite the current projection.
- **`tests/code-interactions.test.tsx`** (new, 4 cases):
  - *CodeView*: authorized repository projection, normalized symbol
    search, authorized-empty versus transport-error behavior, stale
    result removal, and overlapping-query ordering.
  - A generation counter prevents an older Code Graph response from
    replacing the current query's symbols.
- **`tests/workspace-assets-interactions.test.tsx`** (new, 6 cases):
  - *FilesView*: current/history switching, filename-derived safe path,
    classification and lineage metadata, selected detail, and download.
  - *ArtifactsView*: governed kind/query filter, hidden stale detail,
    ACL-classified upload with preview, and refresh preserving selection
    only when the same Artifact ID remains.
- **`tests/workspace-projections-interactions.test.tsx`** (new, 6 cases):
  - *ReportsView*: persisted report and verification counts plus
    refresh-by-ID detail.
  - *DashboardsView*: unique valid panel references, no invented panels,
    and failed-panel guidance cleared by a later successful selection.
  - *SqlView / EvidenceView / TimelineView*: persisted SQL text, Evidence
    envelopes, and Event payloads with their source counts and IDs.
- **`collaboration-view.tsx` mutation handler**: the three ApiError
    branches now refresh first and surface the guidance after. The
    previous order set the message and then called `load()`, which
    clears notices on entry — the guidance vanished before it could
    be read. The version-conflict test asserts the message survives
    the follow-up reload and that the reload actually happened.
- **`runtime-inspector.tsx` tabs**: visual buttons now expose selected
  state, focus order, tab/panel association, and standard keyboard
  navigation. The change does not create a second state source; the
  existing local `tab` value remains authoritative.
- **`actions-view.tsx` modals**: draft creation, approval, and
  preflight/rollback reason surfaces now expose named `dialog` roles
  and `aria-modal=true`. Existing validation, API calls, and backend
  Policy/approval enforcement are unchanged.
- **`studio-view.tsx` selection state**: Agent/Skill/Workflow uses the
  tablist/tab/tabpanel contract and keyboard navigation; every kind or
  version transition clears stale comparison state.
- **`eval-view.tsx` scope and input state**: initial cases are fetched
  once, dataset changes clear baseline/candidate projections, and case/
  run-binding JSON is object-validated before the API boundary.
- **`knowledge-view.tsx` Evidence state**: each search clears prior hits
  before sending the trimmed query; local upload exposes an accessible
  file-input name while preserving classification and ACL metadata.
- **`data-view.tsx` lineage state**: definition, close, and new-lineage
  actions invalidate older requests; tabs implement roving keyboard
  focus and an associated tabpanel.
- **`code-view.tsx` search state**: each normalized query invalidates old
  symbols and older async responses; empty authorized results are not
  conflated with transport errors.
- **`files-view.tsx` / `artifacts-view.tsx` upload state**: hidden inputs
  have accessible names; Files begins without a fabricated path and
  derives one from the selected filename while retaining Workspace
  classification and explicit lineage metadata.
- **`dashboards-view.tsx` panel state**: reference IDs are runtime-checked,
  trimmed, and deduplicated before typed Artifact reads. Selecting or
  closing detail clears prior panel errors; the component never creates a
  substitute CHART/TABLE/SQL panel.
- **Contract wheel build**: `hatchling` is locked as a dev-only dependency;
  the distribution test invokes uv with `--no-build-isolation --offline`
  and continues to inspect the produced wheel's complete frozen contract
  resource set. This removes the hidden PyPI dependency without replacing
  the real build backend or weakening resource equality.
- **Migration CI completion**: the Phase 5 auth-session and Phase 79
  operator-invocation upgrade/downgrade/re-upgrade tests now run in a dedicated
  two-entry PostgreSQL matrix. Each entry owns a fresh database and opt-in flag,
  runs Alembic drift detection, and gates the container candidate job. ADR 0077
  records why destructive migration tests remain isolated.

## Boundary compliance

- Both suites mock only the API boundary (`@/lib/api` via
  `importOriginal` spread); `ApiError` class identity and all types
  are production-identical, and assertions target the exact payloads
  crossing the boundary.
- No new dependency: interactions use `fireEvent` from the already
  pinned Testing Library; `globals: false` stays, with explicit
  `afterEach(cleanup)`.
- Hatchling is a build/test-only locked dependency, not a control-plane
  runtime dependency. The regenerated SBOM records it and its lock closure.
- The collaboration fix reorders existing statements and the Runtime
  inspector change adds browser-native accessibility semantics and
  keyboard handling only — no backend route, schema, permission, or
  API shape changes.
- The release-candidate contract, recorded evidence, and the six
  PENDING operator gates are untouched.
- Destructive migration tests never target the shared migrations service or a
  developer database; their CI services are entry-scoped and disposable.

## Testing

- 67 vitest interaction cases across nineteen surfaces.
- 17 control-plane tests: Workbench, Automation, Admin, Action, Studio,
  Eval, Knowledge, Data, Code, Files, and Artifacts suite surface
  coverage plus Reports/Dashboards/SQL/Evidence/Timeline projection
  markers; accessible tab/dialog contracts; scoped async, input, and
  path validation; the
  notice-survival pin, refresh-before-message ordering in the mutation
  handler, and bookkeeping.
- Full gates: `make check`, `make test-java`, and
  `make validate-release-candidate-contract` (2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates).
- Phase 5 and Phase 79 migration round trips pass against separately created,
  disposable PostgreSQL databases; the CI matrix and container dependency are
  pinned by the Phase 97 contract test.
