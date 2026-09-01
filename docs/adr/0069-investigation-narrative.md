# ADR 0069: Per-stage investigation narrative from persisted keys

## Status

Accepted (Phase 90, 0.90.0-dev)

## Context

The Runtime timeline rendered Run steps as a flat status list. Yet the
investigation stories goal.txt describes (anomaly → trace.search →
git.diff → config.diff → fusion → critic) are correlation stories: the
operator needs to see which step produced which evidence and which
verified conclusion that evidence supports. All three links already
exist as persisted rows — `RunStep.started_at/completed_at`,
`Evidence.step_id` (populated by the Capability Gateway and nullable for
Run-start attachments), and `Claim.evidence_ids` (populated by
verification). The Phase 88 audit listed a per-stage investigation
narrative as a deferred candidate; Phases 88-89 built its prerequisites
(cross-Run attribution guard, typed Evidence detail).

The shaping constraint: a "narrative" tempts summarization, but any
generated prose would be new model output on a frozen surface. The
useful, honest 80% is pure correlation.

## Decision

1. **Correlate only through persisted keys.** Step → evidence via
   `Evidence.step_id`; step → claim via `Claim.evidence_ids` intersected
   with the step's evidence; duration via the persisted timestamp pair.
   No operation names, verdicts, or links are inferred.
2. **Absence is rendered as absence.** A step with no evidence shows no
   chips; missing timestamps show no duration; nothing is backfilled.
3. **Unattributed evidence stays visible.** Rows without `step_id`
   appear in an explicit unattributed section rather than vanishing or
   being force-attributed to a nearby step.
4. **Navigation reuses existing surfaces.** Chips open the Phase 89
   typed Evidence detail; badges jump to the Claims tab; the Phase 88
   cross-Run selection reset still applies.
5. **Bounded display.** Six evidence chips per step with an explicit
   overflow count; the API contract is unchanged.

## Consequences

- The Runtime panel now reads as an investigation chain — plan step,
  duration, produced evidence, supported conclusion — with every element
  traceable to a persisted row.
- A generated investigation narrative (model-written prose) remains a
  separate, explicitly gated future capability rather than something
  smuggled into a rendering phase.
- No endpoint, schema, event, or configuration changed; the six PENDING
  operator gates and promotion eligibility are untouched.
