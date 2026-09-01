# Phase 95 report: schema-driven chart renderer

## What was implemented

The gap audit's "schema-driven chart renderer" item is closed — the
Workbench now renders the Vega-Lite v5 subset the Harness emits for
CHART artifacts instead of forcing every mark into horizontal bars:

- **Line mark**: temporal series render as an SVG line chart with grid
  ticks, point dots with tooltips, and first/last axis labels, sorted
  chronologically regardless of row order.
- **Text mark**: single-number results render as big-number KPI cards
  labeled with the measured field.
- **Bar mark**: nominal series keep the existing horizontal-bar look,
  now schema-driven and capped at 20 points (line at 200).
- **Fail-closed parsing**: unknown marks fall back to bar, content
  without numeric values shows the explicit empty notice, and no data
  series is ever fabricated.
- **Producer contract pinned**: control-plane tests lock
  `_chart_contract` mark selection and numeric normalization so the
  renderer and producer cannot drift.

## Architecture decisions

ADR 0074 records the five decisions: render the emitted subset without
a charting dependency, pure parsing helpers, fail-closed fallback, line
geometry as a pure function, and big-number text marks.

## Migration

None. No backend route, schema, or settings change. Rollback is
reverting the phase commits.

## Validation

- `apps/web/tests/chart-spec.test.ts` — 9 vitest cases over parsing,
  sorting, caps, geometry, and tick formatting.
- `services/control-plane/tests/test_phase95_chart_renderer.py` — 8
  tests: producer contracts, static Web wiring, and bookkeeping.
- `make check` and `make test-java` pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Code Intelligence cross-language precision (P3), post-conclusion
context actions, the operations analytics loop, and full admin CRUD
remain candidates.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase improves artifact rendering
and does not advance promotion.
