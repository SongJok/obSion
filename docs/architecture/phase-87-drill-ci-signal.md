# Phase 87 Alpha.1 drill CI signal architecture review

## Review question

Can restore-path health become a continuous CI-detectable signal without
making external registry health a merge blocker, letting automation overwrite
operator-recorded evidence, introducing credentials into CI, or being mistaken
for promotion authority?

**Status: PASS for a scheduled, credential-free, non-gating detection signal;
PENDING for all six operator gates.**

## Invariants reviewed

- The runtime architecture is unchanged: one Python control plane, one App
  Server, one Harness, Workspace → Thread → Turn → Run → Step → Event, and
  Capability Gateway → Policy → connector for every external access. This
  phase adds one CI workflow file and tests; no runtime or release-tooling
  code changed.
- Trigger discipline: the workflow runs on `schedule` and `workflow_dispatch`
  only. The drills pull pinned images (`pgvector/pgvector`,
  `quay.io/minio/minio`) from external registries, and a registry outage must
  never block a merge — so the drill is a detection signal with one-tick
  latency, not a gate.
- Evidence provenance: fresh ledgers are written to `$RUNNER_TEMP` and
  uploaded as 14-day CI artifacts. The committed ledgers under
  `docs/release/evidence/alpha1/` remain operator-recorded provenance bound to
  their recording commits; automation never overwrites them, and the workflow
  contains no `git commit`/`git push`.
- Credential hygiene: the workflow declares only `OBSION_DR_DRILL=1` and
  `contents: read`. No vendor credentials, no `secrets.*` references, no
  `MINIO_ROOT_*`/`POSTGRES_PASSWORD` values — drill credentials are generated
  per run inside the recorders and never leave process memory, exactly as in
  the operator-driven path.
- Fail-closed signal: the recorders exit non-zero when any check fails, so a
  broken migration, seed, dump, restore, or parity invariant turns the
  scheduled job red without any new classification logic.
- Promotion neutrality: CI-generated ledgers are debug artifacts; they are
  not committed, not referenced by the candidate contract, and never feed
  `promotion_eligible`. All six operator gates remain PENDING.

## Residual scope

Self-hosted runner parity, drill metrics/alerting beyond the GitHub Actions
red/green signal, and restoring from real staging backups remain
operator-owned. The signal detects regressions in the repository-local drill;
it does not evidence a staging restore.
