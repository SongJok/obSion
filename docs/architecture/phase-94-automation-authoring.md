# Phase 94 architecture review: Automation Web authoring depth

## Scope and positioning

The Automation workbench is the operator surface for recurring
intelligence: periodic analysis, human confirmation gates, and
accountable notifications composed into auditable workflows. Until this
phase the Web surface covered only the happy path — create, publish
latest, pause/activate, empty-payload trigger, review — while the
backend already enforced the complete lifecycle. This phase closes that
authoring depth gap entirely on the client side.

## What changed

- **Version management**: the workflow detail panel gains a versions
  card listing every immutable version (newest first) with its step
  summary, creation time, checksum prefix, and publish state. Each row
  offers inspect, derive-new-version, and publish actions; publishing
  an older version is supported as a rollback and remains guarded by
  the backend (`workflow_retired` on retired workflows).
- **Spec viewer**: a read-only modal renders a version's step DAG —
  names, types, dependency edges, prompts, review instructions with the
  self-review flag, and notification content — parsed tolerantly so a
  spec the client cannot parse degrades to an explicit notice instead
  of a crash.
- **Derived authoring**: the new-version modal prefills from any
  version's spec via `draftFromSpec` and saves through
  `buildSpecFromDraft` → `createVersion`, the same builder the create
  modal now uses, so step wiring (`analyze → review? → notify`) exists
  in exactly one place.
- **Trigger payloads**: the manual run action opens a modal accepting a
  JSON object payload with client-side validation and idempotency
  preserved through the existing `web-{uuid}` key; the execution
  drawer echoes the payload when present.
- **Schedule authoring**: schedules can be added after creation with
  cron presets or a validated custom expression, local timezone,
  misfire policy, optional fixed-version pinning, and a JSON input
  payload.
- **Retire**: PAUSED workflows expose a two-step retire; RETIRED
  workflows show a terminal note while runs and audit remain visible.
- **Run provenance and outputs**: the execution drawer labels each
  step's `output_refs` (artifact kinds, notification deliveries) and
  links the child Harness run into the Runtime inspector through the
  Workbench's existing `openRunInspection` path.

## Boundary compliance

- One control plane, one Harness: no backend route, schema, permission,
  or worker change; the automation service's fail-closed validation
  stays the enforcement point.
- Immutable versions: editing always derives a new version; no
  in-place spec mutation path exists anywhere in the client.
- Permissions: every action reuses the existing automation endpoints,
  so `automation.manage` and per-run owner-permission checks are
  unchanged.
- Secrets: payloads are operator-supplied business parameters; no
  credential material is solicited, stored, or rendered.
- The release-candidate contract, recorded evidence, and the six
  PENDING operator gates are untouched.

## Testing

- 18 vitest cases pin the pure helpers: spec building (with/without
  review, defaults), draft round-trip, tolerant spec parsing, payload
  parsing and rejection, cron presets and validation, schedule payload
  building with fixed-version pinning, version sorting, and output-ref
  labeling.
- 10 control-plane tests: live API round-trips for versions,
  re-publish/rollback, trigger payload echo with idempotency replay,
  fixed-version schedules with payloads, retire blocking publication,
  plus static Web wiring assertions and bookkeeping.
