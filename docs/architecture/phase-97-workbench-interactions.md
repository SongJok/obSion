# Phase 97 architecture review: broader Workbench interaction tests

## Scope and positioning

Goal rule 11 requires complete testing of all features. Until this
phase the Web suite covered pure helpers, hooks, and parsers, but no
test mounted a component and drove it the way an operator does — the
Phase 88 audit's last open reliability item. Phase 97 closes it with
an interaction suite over the three most operator-critical surfaces,
and fixes the defect the suite discovered.

## What changed

- **`tests/workbench-interactions.test.tsx`** (new, 10 cases):
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
- **`collaboration-view.tsx` mutation handler**: the three ApiError
    branches now refresh first and surface the guidance after. The
    previous order set the message and then called `load()`, which
    clears notices on entry — the guidance vanished before it could
    be read. The version-conflict test asserts the message survives
    the follow-up reload and that the reload actually happened.

## Boundary compliance

- The suite mocks only the API boundary (`@/lib/api` via
  `importOriginal` spread); `ApiError` class identity and all types
  are production-identical, and assertions target the exact payloads
  crossing the boundary.
- No new dependency: interactions use `fireEvent` from the already
  pinned Testing Library; `globals: false` stays, with explicit
  `afterEach(cleanup)`.
- The component fix reorders existing statements only — no backend
  route, schema, permission, or API shape changes.
- The release-candidate contract, recorded evidence, and the six
  PENDING operator gates are untouched.

## Testing

- 10 vitest interaction cases across the three surfaces.
- 4 control-plane tests: suite surface coverage markers, the
  notice-survival pin, the refresh-before-message ordering in the
  mutation handler, and bookkeeping.
- Full gates: `make check`, `make test-java`, and
  `make validate-release-candidate-contract` (2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates).
