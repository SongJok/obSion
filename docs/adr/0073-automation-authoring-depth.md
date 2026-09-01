# ADR 0073: Automation Web authoring depth

- Status: accepted
- Date: 2026-09-01
- Phase: 94

## Context

The gap audit deferred "Automation Web authoring depth" as its P2 item.
The automation backend has enforced the full lifecycle since the early
phases — immutable workflow versions with checksums, per-version
publishing, trigger input payloads with idempotency keys, schedules with
misfire policy and fixed-version pinning, a retire terminal state, and
step output references — but the Workbench exposed only creation,
publish-latest, pause/activate, an empty-payload trigger, and review
decisions. Operators could not inspect a version's spec, author a new
version, re-publish an older one, pass run parameters, add a schedule
after creation, pin a schedule to a version, retire a workflow, or see
step outputs.

## Decisions

1. **Web-only phase.** Every capability surfaced here already exists in
   the API with fail-closed validation; no backend route, schema, or
   permission changes. This keeps the phase inside the unchanged
   automation contract and reuses its audit guarantees.

2. **Pure authoring helpers.** Spec building, spec-to-draft inversion,
   spec parsing, payload validation, cron presets/validation, schedule
   payload building, and output-ref labeling live in
   `apps/web/src/lib/automation-authoring.ts` as pure functions pinned
   by the vitest behaviour suite. The create modal and the new-version
   modal share `buildSpecFromDraft`, so the two entry points can never
   diverge in step wiring.

3. **Derived drafts, immutable versions.** Editing always starts from
   `draftFromSpec(version.spec)` and saves through
   `POST /workflows/{id}/versions`, producing a new immutable version;
   nothing mutates a published spec in place. Publishing any non-active
   version (including an older one, i.e. rollback) goes through the
   existing publish endpoint, which the backend rejects only for
   RETIRED workflows.

4. **Payloads validated before submission.** Trigger and schedule JSON
   input payloads are parsed client-side with actionable Chinese error
   messages; blank means `{}`, and non-object JSON is rejected because
   the backend contract requires an object. Cron input is validated as
   five fields before submission.

5. **Guarded retire.** Retire is a terminal state, so the action is a
   two-step inline confirm (退役 → 确认退役) available only for PAUSED
   workflows; the arming state resets when the selection changes.

6. **Provenance into the Runtime inspector.** The execution drawer
   renders step `output_refs` (artifact kinds, notification deliveries)
   and turns the child Harness run id into a button that reuses the
   Workbench's `openRunInspection` path — the same cross-view link
   pattern as Collaboration's source Run (ADR 0071), not a second
   loading path.

## Consequences

- The Workbench now covers the whole automation lifecycle the backend
  enforces: create → publish → trigger (with parameters) → schedule
  (with pinning and misfire policy) → version → re-publish/rollback →
  pause → retire, with every version inspectable.
- Idempotency for manual triggers keeps the existing
  `web-{uuid}` key generation; the modal only adds the payload.
- The candidate contract, recorded evidence, and all six PENDING
  operator gates are untouched; nothing in this phase feeds
  `promotion_eligible`.
