# ADR 0066: Restore-path health is a scheduled CI signal, not a merge gate

- Status: Accepted
- Date: 2026-09-01

## Context

ADR 0064 motivated the PostgreSQL drill by observing that the repository had
"no CI-detectable signal when the restore path breaks" — and the first drill
run immediately justified that concern by surfacing a latent trigger defect
invisible to the sqlite-backed suite. Phases 85 and 86 made both halves of the
recovery story executable and recordable, but execution still required an
operator to remember `make record-drill-evidence`. A restore-path regression
merged today would only be discovered at the next manual recording.

## Decision

`.github/workflows/drill.yml` runs both drill ladders on a nightly schedule
plus `workflow_dispatch`. Each run executes `obsion record-drill-evidence` and
`obsion record-artifact-drill-evidence` with `OBSION_DR_DRILL=1` on a
docker-capable hosted runner, writing fresh ledgers to `$RUNNER_TEMP` and
uploading them as 14-day-retention CI artifacts. The recorders' fail-closed
exit codes are the signal: any failed check turns the job red.

Three deliberate constraints shape the design:

- **Not a merge gate.** The drills pull pinned images from external
  registries. A registry outage must never block a pull request, so the
  workflow does not trigger on `push`/`pull_request`; detection within one
  schedule tick is the right trade for a drill.
- **Automation never overwrites recorded evidence.** Committed ledgers under
  `docs/release/evidence/alpha1/` are operator-recorded provenance bound to a
  recording commit. CI-generated ledgers live only in the runner temp
  directory and short-retention artifacts; refreshing the committed ledgers
  remains an explicit operator action.
- **No new authority.** The workflow requests `contents: read`, carries no
  vendor credentials or secrets, and its output never feeds
  `promotion_eligible`; all six operator gates remain PENDING.

## Consequences

- Restore-path regressions (the defect class that produced Alembic revision
  `b88f1c4d5e60`) become visible within a day instead of at the next manual
  drill.
- Debuggability: fresh ledgers are inspectable as CI artifacts without
  reproducing locally.
- No change to the candidate contract, the committed ledgers, or any runtime
  path; rollback is deleting the workflow file.
