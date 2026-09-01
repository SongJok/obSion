# Phase 94 report: Automation Web authoring depth

## What was implemented

The gap audit's P2 item is closed — the Automation workbench now covers
the full workflow lifecycle the backend has always enforced:

- **Versions card**: every immutable version listed newest-first with
  step summary, checksum prefix, and publish state; per-row inspect,
  derive-new-version, and publish (including rollback to an older
  version) actions.
- **Spec viewer**: read-only modal rendering a version's step DAG with
  prompts, review instructions, self-review flag, and notification
  content, degrading gracefully on unparseable specs.
- **Derived authoring**: new versions are authored from any existing
  version's spec through a shared `buildSpecFromDraft` builder — the
  create modal was refactored onto the same helper so step wiring lives
  in exactly one place.
- **Trigger payloads**: manual runs accept a validated JSON object
  payload with the existing idempotency guarantees; the drawer echoes
  the payload.
- **Schedule authoring**: add schedules post-creation with cron presets
  or validated custom expressions, local timezone, misfire policy,
  fixed-version pinning, and input payloads.
- **Retire**: guarded two-step retire for PAUSED workflows with a
  terminal-state note for RETIRED ones.
- **Provenance and outputs**: step `output_refs` render as labeled
  chips and the child Harness run opens in the Runtime inspector via
  the Workbench's existing `openRunInspection` path.

## Architecture decisions

ADR 0073 records the six decisions: web-only phase, pure authoring
helpers, derived drafts over immutable versions, pre-submission payload
validation, guarded retire, and reuse of the Runtime-inspector
provenance link.

## Migration

None. No backend route, schema, settings, or permission change.
Rollback is reverting the phase commits.

## Validation

- `apps/web/tests/automation-authoring.test.ts` — 18 vitest cases over
  the pure helpers.
- `services/control-plane/tests/test_phase94_automation_authoring.py` —
  10 tests: version round-trip and re-publish, trigger payload echo and
  idempotent replay, fixed-version schedule with payload and invalid
  version rejection, retire blocking publication, static Web wiring,
  and bookkeeping.
- `make check` (ruff, contracts, evaluations, secrets scan, eslint,
  tsc, pytest, vitest, alembic, OpenAPI currency) and `make test-java`
  pass.
- `make validate-release-candidate-contract`: 2 live ledgers, 2 drill
  ladders, 16 checks, 6 PENDING operator gates unchanged.

## Deferred findings still open

Code Intelligence cross-language precision (P3), post-conclusion
context actions, the operations analytics loop, full admin CRUD, and a
schema-driven chart renderer remain candidates.

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment,
staging-scoped timed DR drill, registry HIGH CVE policy and signed
promotion, live OIDC/secret-manager/replicas, security and data-owner
sign-off, signed publication). This phase deepens the Automation
workbench and does not advance promotion.
