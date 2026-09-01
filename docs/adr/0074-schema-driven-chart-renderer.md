# ADR 0074: Schema-driven chart renderer

- Status: accepted
- Date: 2026-09-01
- Phase: 95

## Context

The Harness has emitted a Vega-Lite v5 subset for CHART artifacts since
the data-analysis phases: `mark` is `bar`, `{"type": "line", "point":
true}` for temporal categories, or `text` for single-number results,
with `encoding` carrying x (temporal/nominal), y (quantitative), and
text fields. The Workbench's `ChartPreview` ignored `mark` entirely and
rendered everything as horizontal bars — temporal success-rate series
lost their chronological shape, and single-number results rendered as a
degenerate one-row bar. The gap audit tracked this as the deferred
"schema-driven chart renderer" item.

## Decisions

1. **Render the emitted subset, not a charting library.** The backend
   only ever emits bar, line+point, and text marks, so the renderer
   implements exactly that subset as dependency-free SVG/CSS. Pulling
   in a full Vega-Lite runtime would add a heavy client dependency to
   render three mark types and would execute arbitrary specs; the
   narrow renderer keeps the trust boundary explicit — only the
   Harness-produced subset is honored.

2. **Pure parsing helpers.** `apps/web/src/lib/chart-spec.ts` parses
   the spec tolerantly (`mark` as string or object, missing encodings),
   normalizes numeric points, sorts temporal x values chronologically
   (the producer preserves row order; the renderer must not trust it),
   and caps points (20 bars, 200 line points) so a pathological result
   set cannot hang the page.

3. **Fail-closed fallback.** Unknown mark types render as bars (the
   generic Vega-Lite default) while `declaredMark` is preserved for
   diagnostics; content with no numeric values renders the existing
   explicit empty notice — never fabricated data.

4. **Line geometry as a pure function.** `buildLineGeometry` maps
   points into a padded viewBox (path, dots, y ticks) so the behaviour
   suite pins the math without a DOM; the component only projects the
   geometry into SVG.

5. **Text mark as big numbers.** Single-number results render as
   big-number cards labeled with the y/text field, matching how
   operators read KPI-style results.

## Consequences

- Temporal line charts render chronologically with grid ticks and
  point tooltips; bar charts keep the existing horizontal-bar look;
  single-number results get a KPI treatment.
- Dashboard panels and artifact previews share the same renderer
  through `ArtifactPreview`, so both surfaces improve together.
- No backend change; the producer contract is pinned by tests so the
  renderer and producer cannot drift.
- The candidate contract, recorded evidence, and all six PENDING
  operator gates are untouched.
