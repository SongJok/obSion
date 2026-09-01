# Phase 95 architecture review: schema-driven chart renderer

## Scope and positioning

CHART artifacts are how governed data results become visual: the
Harness derives a Vega-Lite v5 subset from cited data evidence and the
Workbench renders it. The renderer must honor the producer's schema —
a temporal series drawn as bars is a rendering bug that misrepresents
the evidence. This phase makes the renderer schema-driven across every
surface that shows artifacts (Artifacts, Dashboards, run previews).

## What changed

- **`chart-spec.ts`**: tolerant parsing of the emitted subset — mark as
  string or object, x/y/text encodings, numeric normalization,
  chronological sorting for temporal x, point caps (20 bars / 200 line
  points), and a fail-closed fallback (unknown mark → bar, no numeric
  values → explicit empty notice).
- **`ArtifactPreview`**: `ChartPreview` now dispatches on the parsed
  mark — horizontal bars (unchanged look), an SVG line chart with grid
  ticks, dots with tooltips, and first/last axis labels, or big-number
  cards for text marks.
- **Geometry as pure math**: `buildLineGeometry` produces the path,
  dots, and ticks for a padded viewBox, pinned by the behaviour suite
  without a DOM.
- **Producer contract pinned**: control-plane tests lock
  `_chart_contract` mark selection (temporal → line+point, nominal →
  bar, single number → text) and numeric normalization so renderer and
  producer cannot drift.

## Boundary compliance

- Rendering derives only from persisted CHART artifact content; no data
  series is fabricated, and content without numeric values shows the
  explicit notice.
- The renderer implements only the subset the Harness emits; it does
  not execute arbitrary Vega-Lite specs, keeping the client trust
  boundary narrow and dependency-free.
- No backend route, schema, or permission change.
- The release-candidate contract, recorded evidence, and the six
  PENDING operator gates are untouched.

## Testing

- 9 vitest cases: temporal parsing and chronological sorting, nominal
  bar order and the 20-point cap, text mark, string mark and unknown
  mark fallback, malformed-content rejection, the 200-point line cap,
  line geometry (viewport mapping, single point, flat series), and tick
  formatting.
- 8 control-plane tests: producer mark/encoding contracts, fail-closed
  producer behavior, static Web wiring, and bookkeeping.
