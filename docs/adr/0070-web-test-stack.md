# ADR 0070: JavaScript component-test stack for apps/web

## Status

Accepted (Phase 91, 0.91.0-dev)

## Context

ADR 0067 kept the frontend verification pattern of its phase — Python
static boundary tests plus lint/typecheck/build — and deferred "a
JavaScript component-test stack" as a separate decision rather than
bundling it into a hardening pass. Three phases later the deferred
question is ripe: Phases 88-90 added real client logic with branching
behavior (request normalization, envelope classification, per-stage
correlation) that static source assertions can pin textually but not
execute. The desktop, IDE, and TS SDK workspaces already run executable
`node:test` suites; apps/web was the only workspace without one.

Constraints: dependencies must be exact-pinned (repository convention),
must not enter the production bundle or the release artifact manifest,
and must not weaken the Python static suites that pin cross-file
contracts.

## Decision

1. **vitest 4 + jsdom + Testing Library, dev-only.** Exact-pinned
   devDependencies of `@obsion/web`; esbuild transforms TSX, so no Babel
   or SWC plugin chain is added.
2. **Tests live in `apps/web/tests/`**, discovered by
   `vitest.config.mts`, with the `@/` alias mapped and globals off so
   test files type-check identically to app code under the existing
   `tsc --noEmit` and eslint gates.
3. **Executable coverage targets logic, not snapshots.** The suite
   asserts classifier dispatch, accessor no-invention behavior, API
   error normalization, session transport invariants, and rendered typed
   Evidence output — behavior that would silently regress otherwise.
   Pixel-level or snapshot testing is deliberately not introduced.
4. **Python static suites remain.** They pin cross-file contracts (e.g.
   "the inspector consumes EvidenceContent"); the vitest suite adds
   executable coverage beneath them, not a replacement.
5. **Zero wiring changes.** The workspace `test` script joins the
   existing root `npm test --workspaces --if-present` fan-out that
   `make test` and CI already execute.

## Consequences

- apps/web is no longer the untested workspace: 34 executable tests run
  in ~1s locally and in CI alongside the other workspaces.
- Regressions in the Phase 88-90 reliability, typed Evidence, and
  narrative logic now fail at behavior level, not just text level.
- The production bundle, release artifacts, and candidate contract are
  untouched; the six PENDING operator gates are unaffected.
- Broader Workbench interaction coverage (routing, streaming, forms) is
  a later increment on the same stack, not a new decision.
