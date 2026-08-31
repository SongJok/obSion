# ADR 0064: Alpha.1 backup/restore readiness is an executed, recorded drill

- Status: Accepted
- Date: 2026-08-31

## Context

The operator runbook documented PostgreSQL backup and restore as prose, and the
Alpha.1 candidate contract correctly keeps the `backup-restore-drill` gate
operator-owned: a staging-scoped timed restore cannot be fabricated from a
repository. But "documented" is weaker than "executed". PostgreSQL is the
transactional source of truth, and the repository had no automated proof that a
migrated, seeded database survives a `pg_dump`/`pg_restore` round trip — nor any
 CI-detectable signal when the restore path breaks. The first execution of such
a drill immediately justified itself: it surfaced a latent trigger defect that
failed every `verification_assessments` insert on real PostgreSQL while the
opt-in invariant suite stayed silent.

## Decision

Backup/restore readiness becomes a declared ladder plus a recorded ledger,
mirroring ADR 0063's live-evidence shape without touching vendor systems.

`docs/release/alpha1-drill-evidence-contract.yaml` (`DrillEvidenceLadder`)
declares eight ordered checks, the pinned `pgvector/pgvector:0.8.6-pg17-bookworm`
image (identical to docker-compose), and minimum row thresholds for the Harness,
Evidence, Claim, and audit tables.

`obsion record-drill-evidence` (module `obsion.release.drill`) requires
`OBSION_DR_DRILL=1` and docker. It starts two throwaway containers with
per-run generated credentials, migrates the source with Alembic, seeds a real
governed scenario through the control-plane REST API (workspace, knowledge
document, thread, completed Run), dumps with `pg_dump --format=custom`,
restores into the fresh target with `pg_restore --exit-on-error`, and verifies
schema-version parity, full row-count parity, referential integrity, and
ordered audit-identity preservation. Once a stage fails, every downstream check
is recorded `failed` — a skip is never a pass, and partial progress can never
masquerade as evidence.

The ledger (`DrillEvidenceLedger`) records check classifications with bounded
content-free details, stage timings, the dump size and SHA-256, the Alembic
head, and per-table row counts — never credentials, DSNs, hosts, or ports.
Ledger validation rejects failed checks, forbidden keys, credential-shaped
values, tampered checksums, and row-count shortfalls against the contract
minimums.

The candidate contract gains a `drillEvidence` section validated offline. As
with live evidence, recorded drill output is readiness input only: it never
feeds `promotion_eligible`, and the staging-scoped `backup-restore-drill`
operator gate remains PENDING until a real staging restore is evidenced.

## Consequences

- The latent defect is fixed by Alembic revision `b88f1c4d5e60`, which replaces
  the `obsion_validate_verification_assessment()` body so candidate-id
  resolution happens in separate branch statements instead of one CASE
  expression whose `NEW.assessment_id` reference failed at plan time on the
  parent table. The verification rules are unchanged; the drill's green ledger
  now proves a real governed Run completes against migrated PostgreSQL 17.
- Restores are proven against real dumped bytes, not model reconstructions;
  row-count parity covers every public table, not a curated subset.
- The drill is self-cleaning and safe to run on any docker-capable host; it
  never touches deployed infrastructure.
- Object-storage artifact restore remains documented prose; the drill scope is
  the PostgreSQL source of truth, and the staging gate keeps its operator
  ownership.
