# Phase 91 web component-test stack architecture review

## Review question

Can apps/web gain an executable JavaScript test stack — the decision ADR
0067 deferred — without shipping new runtime dependencies, weakening the
Python static contract suites, or touching the frozen Alpha.1 surface?

**Status: PASS for a dev-only executable test stack; PENDING for all six
operator gates.**

## Invariants reviewed

- **Runtime architecture unchanged**: one Python control plane, one App
  Server, one Harness; this phase adds devDependencies and test files
  only — no application source changed behavior.
- **Supply-chain discipline**: all four new packages (vitest, jsdom,
  @testing-library/react, @testing-library/jest-dom) are exact-pinned in
  `apps/web/package.json` with the lockfile updated; they are
  devDependencies excluded from the Next.js production build, and the
  release artifact manifest (`make release-artifacts`) is unchanged.
- **Verification pattern strengthened, not replaced**: Python static
  boundary suites still pin cross-file contracts; vitest adds executable
  coverage for pure logic and component rendering beneath them. Both run
  in the same gates (`make check` → `npm test`, CI quality job).
- **Tests assert real contracts**: API tests use the backend's actual
  error envelope (`code`/`message`/`correlation_id`); component fixtures
  mirror the normalized Evidence envelopes the control plane persists;
  no test mocks the backend into a shape it does not produce.
- **Session invariants re-verified executably**: the suite asserts
  `credentials: "include"` and `cache: "no-store"` on every request and
  never touches browser token storage.

## Boundary confirmation

- No endpoint, schema, capability, policy, or event change; the
  candidate contract and recorded evidence are untouched.
- Broader Workbench interaction coverage is a later increment on this
  stack, not a new decision; post-conclusion context actions, operations
  analytics, admin CRUD, and the chart renderer remain deferred
  candidates.

## Verification

- `npm test --workspace @obsion/web` — 34/34 pass; root `npm test`
  passes across all workspaces.
- `services/control-plane/tests/test_phase91_web_test_stack.py`
  (7 tests) pins the stack shape and suite coverage.
- Web typecheck, lint, and production build pass.
- `make check`, `make test-java`, and
  `make validate-release-candidate-contract` pass on the final tree.
