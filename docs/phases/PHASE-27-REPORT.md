# PHASE-27-REPORT — Harness REFLECT as a first-class RunStep

## What was implemented

Phase 27 closes the Harness loop gap against goal.txt: Verify is followed by a
durable Reflect step, then Respond.

- `StepKind.REFLECT` is part of the domain enum, OpenAPI, and PostgreSQL CHECK on
  `run_steps.kind` (Alembic `d27a8c1e4f90`, which also admits the already-used
  `OBSERVE` value).
- `_prepare` inserts REFLECT between VERIFY and RESPOND. RESPOND depends on REFLECT.
- `_respond` starts and completes REFLECT after Critic, recording
  `reflect.respond` or `reflect.withhold` before Claims and the answer Artifact.
- Missing-evidence replan shifts VERIFY, REFLECT, and RESPOND together and rewires
  `depends_on`.
- Workbench timeline labels REFLECT. CLI and SDKs already project `kind` from REST.
- ADR 0006 records that Reflect is a persisted step, not a hidden `_respond` branch.

## Architecture decisions

Reflect does not emit a new Event type. Publication stays on the Phase 20 Critic
assessment (VERIFIED/PARTIAL, PUBLISH/WITHHOLD). Evidence-gap replan remains the
Phase 23 loop that runs before `_respond`. A later phase can attach critic-failure
replan to the REFLECT decision without changing the step graph again.

Legacy Runs constructed without a REFLECT row still publish: core-step helpers no-op
when the step is missing.

## Validation

- `uv run pytest --no-cov` — 435 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase27_harness_reflect.py` and updated Phase 7/23/26 step graphs.
- OpenAPI `StepKind` includes `REFLECT`. Error-producer line maps for `runtime.py`
  were updated after the inserted Reflect helpers.
- `uv run ruff check .`, mypy on the Harness runtime, Workbench `tsc --noEmit`, and
  `uv run obsion scan-secrets` — 0 findings.
- Local `alembic check` needs PostgreSQL; CI remains the operator path for that gate.

## Remaining risks

- Historical PostgreSQL rows created before OBSERVE existed are compatible after this
  CHECK widen; a true downgrade maps REFLECT back to VERIFY.
- Critic-failure replan after REFLECT is intentionally not in this phase.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
