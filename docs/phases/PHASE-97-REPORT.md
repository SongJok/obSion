# Phase 97 report: broader Workbench interaction tests

## What was implemented

The Phase 88 audit's last open reliability item — broader Workbench
interaction tests — is closed:

- **Interaction suite**: `tests/workbench-interactions.test.tsx`
  mounts real components in jsdom and drives them with `fireEvent`
  and accessible queries, mocking only the `@/lib/api` boundary via
  `importOriginal` spread (types and `ApiError` identity stay
  production-identical).
- **Root orchestration reliability suite**:
  `tests/workbench-orchestration-interactions.test.tsx` mounts the complete
  Workbench and deterministically reverses Workspace, Thread, and source-Run
  request completion. Selection-sensitive loaders capture immutable identity
  snapshots, validate generation plus resource ownership, and commit a whole
  inspection only after every Event, Step, Evidence, Memory, Conversation,
  Claim, Artifact, and feedback record agrees with the selected Run and
  Workspace. Independent Context/upload generations and guarded submit,
  cancel, replay, feedback, and App Server stream paths prevent stale data,
  errors, loading, and pending indicators from leaking into a newer context.
- **Automation interaction suite**:
  `tests/automation-interactions.test.tsx` drives the version,
  trigger, schedule, retirement, and immutable-authoring lifecycle
  through the mounted `AutomationView` and the same production-typed
  API boundary.
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
- **Runtime inspector depth**: all six inspector tabs expose the
  tablist/tab/tabpanel contract, keep one tab in the keyboard focus
  order, support ArrowLeft/ArrowRight/Home/End navigation, and have
  interaction coverage for context, Evidence and Artifact details,
  governed Memory, and Claim-to-Evidence navigation.
- **Automation coverage**: overview/detail loading, rollback-style
  publication of an older immutable version, validated manual trigger
  payloads, malformed JSON rejection before transport, unpinned
  schedule creation, guarded two-step retirement, and derivation of a
  new immutable version.
- **Governance-console coverage**: the mounted AdminView verifies that
  one failed governance domain degrades independently, stable IM sender
  identifiers are bound and revoked with a refreshed projection, and
  Connector SDK health/discovery/scan/promotion calls preserve the
  production API contract. Discovery is explicitly verified not to
  auto-bind a Capability.
- **Governed-action coverage**: ActionsView creates only development or
  staging PR drafts, preserves the client idempotency key, requires an
  operator-authored preflight declaration, sends an independent
  approval decision with its reason, requests rollback with a bounded
  human reason, and cancels only through the governed Action endpoint.
  The form is explicitly verified not to expose a production option.
- **Studio coverage**: accessible Agent/Skill/Workflow tabs, Workflow
  validation-only behavior, immutable Skill publication, explicit
  promotion and rollback, and version comparison with
  `traffic_split=false`. Kind/version switches clear stale baselines.
- **Eval coverage**: one initial case fetch, dataset-scoped baseline and
  candidate state, schema-shaped case input, pinned Agent/Prompt/model/
  baseline/Run bindings, distinct same-dataset comparison, and stable
  fail-fast validation for malformed or non-string binding JSON.
- **Knowledge coverage**: trimmed authorized search, recorded connector
  provenance, stale Evidence removal before a new request, ACL-bearing
  local upload, vendor-specific document ingestion, and bounded Feishu
  knowledge-space sync summaries.
- **Data coverage**: verified metric filtering, governed definition
  details, accessible definition/lineage tabs, read-only source lineage,
  explicit lineage errors, and generation-guarded metric switching so a
  slower old response cannot be attributed to the current metric.
- **Code coverage**: authorized repository count, normalized symbol
  search, empty-result versus transport-failure semantics, immediate
  stale-symbol removal, and generation guards so a slower query cannot
  overwrite the current Code Graph projection.
- **Workspace asset coverage**: current versus immutable file history,
  filename-derived safe default paths, ACL-classified uploads, governed
  downloads, Artifact kind/query filtering, preview selection, upload,
  and refresh-by-ID detail preservation.
- **Workspace projection coverage**: Reports render persisted REPORT
  artifacts and Critic verification; Dashboards resolve only unique valid
  Artifact references; SQL renders persisted validated text without
  execution; Evidence renders immutable envelopes; Timeline renders Event
  payloads and distinct Run counts from the shared Event Store.
- **Offline contract distribution**: Hatchling is a locked dev build
  dependency, and the frozen-contract wheel test runs the real PEP 517
  backend with `--no-build-isolation --offline`. A fresh temporary output
  directory is still inspected byte-for-byte; PyPI availability is no
  longer an accidental test prerequisite. The CycloneDX SBOM is regenerated
  from the updated lock.
- **Complete migration CI coverage**: Phase 5 browser-session and Phase 79
  operator-invocation migration tests now execute in a two-entry CI matrix.
  Each job owns a fresh PostgreSQL database, sets only its paired opt-in flag,
  exercises upgrade/downgrade/re-upgrade, runs Alembic drift detection, and must
  pass before the Alpha.1 container candidate job starts.

## Defect found and fixed

Writing the collaboration flow test surfaced a real UX defect: the
mutation handler surfaced its actionable message and *then*
refreshed, and the refresh clears notices on entry — so
version-conflict, assignee-invalid, and source-Run mismatch guidance
vanished before anyone could read it. The handler now refreshes
first and surfaces the message after; the version-conflict test pins
the ordering by asserting the guidance survives the follow-up reload.
The inspector pass also found an accessibility gap: its visual tabs
were plain buttons inside a `tablist`, with neither tab semantics nor
keyboard navigation. The buttons now implement the complete selected,
focus, navigation, and panel association contract.
The Action pass found the same class of gap in three high-risk modal
surfaces: draft creation, approval, and preflight/rollback reasons had
no dialog name or modal semantics. All three now expose named
`dialog` contracts with `aria-modal=true`, so assistive technology can
distinguish the active decision surface from background actions.
The Studio pass found stale comparison state crossing kind/version
boundaries; it is now reset at every selection boundary. The Eval pass
found a duplicate initial case request plus stale cross-dataset baseline
state; both are removed, candidate=self is excluded, and JSON inputs are
validated as objects before transport.
The Knowledge pass found that a failed second search left the first
query's Evidence visible; results now clear before the normalized new
query is sent. The Data pass found unguarded asynchronous lineage state;
requests are now generation-scoped, closing or switching invalidates an
older response, and the detail tabs implement keyboard semantics.
The Code pass found the same cross-request race and stale-result issue;
symbol searches now clear first, trim once, and update only their own
generation. The Files pass found a hard-coded `notes.txt` default that
could misname an unrelated upload; an empty path now derives safely from
the selected filename, and both upload inputs have accessible names.
The Dashboard pass found that duplicate/non-string panel references were
accepted and an earlier panel error survived a later successful selection.
References are now string-validated, trimmed, deduplicated, and every new
detail selection clears the prior panel error before loading.
The full gate then exposed that contract wheel validation created an empty
uv cache and downloaded Hatchling during the test. A PyPI timeout failed an
otherwise valid build. Locking the backend in the dev environment and using
offline/no-isolation mode fixes the reproducibility boundary without
weakening the packaged-resource equality assertion.
The comprehensive migration audit also found that two destructive round-trip
tests existed but were never opted into by CI. Both passed against independent
disposable PostgreSQL databases, and the new matrix makes those proofs
mandatory without risking a shared development database.

The root-orchestration reliability amendment found a P1 attribution hazard:
async inspection requests directly updated independent React projections, so a
slower old Workspace, Thread, or source Run could overwrite the current Run's
Claims, Evidence, and Artifacts. Workbench now constructs a complete immutable
snapshot, validates the captured selection generation and every persisted
owner field, and only then commits. Workspace changes invalidate selection,
context, upload, feedback, and lifecycle operations; stale catch/finally paths
cannot overwrite current errors, loading, or pending indicators. Source-Run
reads stop early when superseded, stream Events are checked against the pinned
Run, and feedback returned for another Run fails closed. Submission uses a
synchronous single-flight guard, newly created Threads remain uncommitted until
the first Turn/Run exists, reopening an active Run restarts App Server streaming
and REST reconciliation, and Collaboration source Runs open through their owning
Thread. Same-Thread inspection keeps the last verified projection until the new
snapshot passes ownership validation. Eighteen mounted, deferred-promise
interactions pin these boundaries.

## Architecture decisions

ADR 0076 records fourteen Workbench decisions: Testing Library interactions
with no new dependency, one mocked API boundary per suite, explicit
cleanup under `globals: false`, and treating a failing interaction
test as a defect report, plus the accessible Runtime inspector tab
contract, named Action dialogs, Studio selection-scoped comparison,
dataset-scoped Eval state/input validation, stale-Evidence prevention,
generation-scoped metric lineage, generation-scoped Code search, and
path/ACL-governed Workspace assets, and persisted-fact-only Workspace
projections, and offline reproducible contract-wheel validation.
ADR 0077 records the separate migration-test isolation decision.

## Migration

None. No backend route, schema, permission, or API shape change.
Hatchling is added to the development-only dependency set and `uv.lock`;
there is no runtime dependency or database migration. The CI change only
executes existing migration tests in disposable databases. Rollback is
reverting the phase commits.

## Validation

- `apps/web/tests/workbench-orchestration-interactions.test.tsx` plus the
  existing Workbench, Automation, Admin, Action, Studio, Eval, Knowledge,
  Data, Code, Workspace asset, and Workspace projection interaction suites —
  85 cases across twenty operator-critical surfaces; the full Web suite passes
  181 tests in 22 files.
- `services/control-plane/tests/test_phase97_workbench_interactions.py`
  — 18 tests: root Workbench generation/ownership markers plus Automation,
  Admin, Action, Studio, Eval, Knowledge, Data, Code, Files, and Artifacts suite
  coverage; Reports/Dashboards/SQL/Evidence/Timeline fact projections;
  accessible tab/dialog contracts; immutable inspection snapshots, scoped
  async/input/path pins, stream and feedback ownership, notice survival,
  refresh-before-message ordering, and bookkeeping.
- `test_postgres_phase5_auth_session_migration.py` and
  `test_postgres_operator_invocation_migration.py` pass in separate disposable
  PostgreSQL databases; `.github/workflows/ci.yml` now reproduces both through
  the `migration-round-trips` matrix before candidate artifact construction.
- `make check` and `make test-java` pass.
- `services/control-plane/tests/test_contract_distribution.py` builds the
  wheel offline and confirms every frozen Event/Error JSON resource is
  packaged exactly once.
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
