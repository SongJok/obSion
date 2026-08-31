# Phase 87 report: Alpha.1 drill CI signal

## What was implemented

Phase 87 closes the gap ADR 0064 named — "no CI-detectable signal when the
restore path breaks" — by running both drill ladders on a schedule:

- `.github/workflows/drill.yml` triggers nightly (`17 3 * * *`) and on
  `workflow_dispatch`, on docker-capable `ubuntu-24.04` hosted runners with a
  30-minute timeout and `OBSION_DR_DRILL=1`.
- Each run executes `obsion record-drill-evidence` (PostgreSQL ladder) and
  `obsion record-artifact-drill-evidence` (object-store ladder). Fresh ledgers
  go to `$RUNNER_TEMP` and are uploaded as the `drill-ledgers` artifact with
  14-day retention, `if: always()`, and `if-no-files-found: error` — a missing
  ledger is itself a red signal.
- The recorders' fail-closed exit codes are the whole signal: any failed
  check among the 16 ladder checks turns the job red, with no new
  classification logic in CI.
- `services/control-plane/tests/test_phase87_drill_ci_signal.py` (7 tests)
  pins the trigger discipline, credential absence, evidence provenance,
  action pinning, and release/status bookkeeping.

## Architecture decisions

- **Detection signal, not a gate** (ADR 0066): no `push`/`pull_request`
  trigger, because the drills pull pinned registry images and external
  registry health must never block a merge.
- **Automation never overwrites recorded evidence**: committed ledgers under
  `docs/release/evidence/alpha1/` remain operator-recorded provenance bound to
  their recording commits; CI ledgers live only in the runner temp directory
  and short-retention artifacts.
- **No new authority**: `permissions: contents: read`, no secrets, no
  `git push`/`docker push`, and CI-generated ledgers never feed
  `promotion_eligible`.

## Migration

None. No schema, settings, or runtime changes; the workflow becomes active on
its next schedule tick after merge. Rollback is deleting the workflow file.

## Validation

- Phase suite: `test_phase87_drill_ci_signal.py` (7 tests) plus rolled-forward
  Phase 82–86 suites.
- Repository quality gate: `make check` (format, lint, mypy, full pytest with
  coverage, migration-check) and `make test-java`.
- Release tooling: `make validate-release-candidate-contract` against the
  restructured two-ladder `drillEvidence` block (2 ledgers, 16 checks).

## Remaining operator gates

All six Alpha.1 candidate gates remain PENDING (staging deployment, UAT,
staging-scoped timed DR drill, live OIDC / secret-manager validation, signed
image/tag, external publication). The CI drill detects repository-local
restore-path regressions; it does not evidence a staging restore and does not
advance promotion.
