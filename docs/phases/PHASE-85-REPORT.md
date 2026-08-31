# PHASE-85-REPORT — Alpha.1 backup/restore drill evidence

## What was implemented

- Added `docs/release/alpha1-drill-evidence-contract.yaml`
  (`DrillEvidenceLadder`): eight ordered checks, the pinned pgvector/pg17
  image shared with docker-compose, and minimum row thresholds across the
  Harness, Evidence, Claim, and audit tables.
- Added `obsion.release.drill`: contract loading, two throwaway PostgreSQL
  containers with per-run credentials, Alembic migration, real REST scenario
  seeding, custom-format `pg_dump` with SHA-256, `pg_restore --exit-on-error`
  into a fresh target, and four parity verifications (schema version, row
  counts, referential integrity, ordered audit identities). Ledger emission is
  redacted and canonically checksummed; offline validation rejects failed
  checks, forbidden keys, credential-shaped values, tampering, and row-count
  shortfalls.
- Added `obsion record-drill-evidence` and `make record-drill-evidence`. Both
  fail closed (exit 2) without `OBSION_DR_DRILL=1`; the Make target also
  requires docker. `.env.example` documents the opt-in.
- Extended `docs/release/alpha1-candidate-gates.yaml` with a `drillEvidence`
  section and taught `validate_release_candidate` to validate it offline. The
  summary reports `drill_evidence_ledgers`/`drill_evidence_checks`; promotion
  eligibility is untouched and the `backup-restore-drill` operator gate remains
  PENDING.
- Fixed the latent defect the drill discovered: Alembic revision
  `b88f1c4d5e60` replaces the verification-admission trigger body so
  candidate-id resolution no longer fails at plan time on
  `verification_assessments` inserts.
- Recorded one real drill ledger at revision `d4c6650` (8/8 checks passed).

## Architecture decisions

ADR 0064 records the core decisions: backup/restore readiness is an executed,
recorded drill rather than prose; seeding goes through the production REST API
so dumped data exercises real write paths; a skip is never a pass (upstream
failure cascades to every downstream check); drill credentials never leave
process memory; and recorded drill evidence never feeds `promotion_eligible`.

No runtime path changed beyond the corrective migration: the one Python
control plane, one App Server, durable Harness hierarchy, Capability Gateway,
Policy, Evidence, and credential boundaries remain unchanged.

## Migration

Alembic revision `b88f1c4d5e60` (`CREATE OR REPLACE FUNCTION
obsion_validate_verification_assessment()`): the CASE-based candidate-id
resolution is split into branch statements because plpgsql validates record
field references when planning the expression, which made every
`verification_assessments` insert fail at COMMIT on real PostgreSQL. Table DDL
is unchanged, the verification rules are byte-identical, and a
downgrade/upgrade round trip was verified on a real PostgreSQL 17 container.
Alembic drift validation remains part of the phase gate.

## Validation

- `test_phase85_dr_drill_evidence.py` (24 tests) covers the ladder contract and
  image pinning, opt-in gating, happy-path classification and checksum,
  credential redaction, fail-closed cascades (no docker, migration failure,
  seeder failure, minimum-row shortfall, empty dump, restore error, schema
  divergence, count mismatch, orphans, audit divergence), tamper/forbidden/
  shortfall detection, the real recorded ledger, candidate-gate binding,
  fail-closed Make target and CLI, release notes, and project status.
- The sqlite-backed seeder test proves the scenario meets every contract
  threshold table; historical Phase 82, 83, and 84 suites continue to pass
  with the CLI default release manifest now `0.85.0-dev`.
- `make check` covers Ruff formatting/lint, strict mypy, contract/Event/
  evaluation/release validation including the new `drillEvidence` section,
  secret scanning, all Python and frontend tests, and Alembic drift.
- Live drill with docker (self-cleaning, credentials never persisted),
  revision `d4c6650`: all eight checks passed — migrated to head
  `b88f1c4d5e60`, 10 threshold tables seeded, 458,132-byte custom dump
  restored cleanly, 89-table row-count parity, zero orphans, identical audit
  identities, total 27.5s.
- `obsion validate-release-candidate --contract-only` reports
  `drill_evidence_ledgers: 1`, `drill_evidence_checks: 8`,
  `promotion_eligible: false`, and the six pending operator gates.

## Remaining operator gates

- Clean staging/UAT, staging-scoped timed PostgreSQL/object-store restore,
  registry HIGH/CRITICAL CVE policy and signatures, live OIDC/secret
  manager/read replicas, security/data-owner approval, and
  maintainer-authorized publication remain `PENDING`; recorded drill evidence
  is readiness input, not promotion authority.
- The drill covers the PostgreSQL source of truth; object-storage artifact
  restore remains documented prose pending an operator drill with a real
  bucket.
- Ledgers are point-in-time audit records at one revision; refreshing them is
  an explicit operator action via `make record-drill-evidence`.
