# Phase 89 report: typed Evidence views

## What was implemented

Phase 89 delivers goal.txt section 57's Evidence Panel on real persisted
data, acting on the highest-value deferred finding from the Phase 88
experience audit without expanding the frozen Alpha.1 product surface:

- **Classifier** (`apps/web/src/lib/typed-evidence.ts`): dispatches on
  `evidence_type` plus the normalized content envelope — `events[]`,
  `items[]`, `columns`/`rows`, `plan`/`validation`, `hits`, `text` —
  with type-guard accessors and display bounds (100 table rows, 200
  list entries, 12 attribute chips). `CODE` items get a dedicated
  projection; everything unrecognized stays generic.
- **Typed renderers** (`apps/web/src/components/evidence-content.tsx`):
  - Observability event streams for METRIC / LOG / TRACE /
    `deployment.list` with severity chips, service/environment/time
    headers, and bounded attribute projections.
  - Engineering change items for GIT / CONFIG / `deployment.commit` /
    `k8s.status`, with `git.diff` patch rendering and `config.diff`
    before/after pairs.
  - Code Graph symbol cards with `path:start-end` locations, kind,
    language, repository, and commit.
  - Real HTML tables for query results and a plan + validation ledger
    for `sql.explain`.
  - Knowledge citation provenance (moved from the inspector) and
    readable document text for attachments.
  - Raw JSON fallback preserved for every other payload.
- **Metadata ledger** (`EvidenceMeta`): `observed_at`, `ingested_at`,
  confidence, classification, permissions, content fingerprint,
  `step_id`, `run_id`, and lineage — only persisted fields.
- **Type honesty**: the Web `Evidence` interface now matches the REST
  projection exactly (`run_id` required, `step_id`/`ingested_at`
  present); the impossible "无 Run" branch was removed.
- **Integration**: Runtime inspector detail and the workspace Evidence
  page share `EvidenceContent`/`EvidenceMeta`; new CSS block under
  "Typed Evidence views (Phase 89)".

## Architecture decisions

ADR 0068 records the five decisions: envelope-driven dispatch, type-guard
accessors with no fabricated fields, metadata ledger from persisted
fields only, UI display bounds on top of server budgets, and keeping the
established static-test verification pattern.

## Migration

None. No schema, settings, API, or runtime changes. Rollback is
reverting the phase commits.

## Validation

- Phase suite: `test_phase89_typed_evidence.py` (10 tests) plus
  rolled-forward Phase 82-88 bookkeeping suites.
- Web typecheck, lint, and production build pass.
- Repository quality gate: `make check` (ruff format, contracts,
  evaluations, release notes, candidate contract, datasets, secret scan,
  eslint, tsc, full pytest, node:test suites, alembic check).
- `make test-java` and `make validate-release-candidate-contract` pass;
  2 live ledgers, 2 drill ladders, 16 checks, 6 PENDING operator gates
  unchanged.

## Deferred findings still open

From the Phase 88 audit: per-stage investigation narrative in the Runtime
panel, post-conclusion context actions (view code/logs/SQL, generate
report, create issue), the operations analytics loop, full admin CRUD, a
schema-driven chart renderer, and a JavaScript component-test stack for
`apps/web`.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase changes rendering only and
does not advance promotion.
