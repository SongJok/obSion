# PHASE-86-REPORT — Alpha.1 artifact-store drill evidence

## What was implemented

- Added `docs/release/alpha1-artifact-drill-evidence-contract.yaml`
  (`ArtifactDrillEvidenceLadder`): eight ordered checks, the pinned MinIO and
  pgvector/pg17 images shared with docker-compose, and a minimum object
  threshold.
- Added `obsion.release.artifact_drill`: contract loading, one throwaway
  PostgreSQL plus two throwaway MinIO containers with per-run credentials,
  Alembic migration, real REST scenario seeding (knowledge-document ingest,
  workspace file artifact upload, content roundtrip) through the production
  `MinioObjectStore` write path, a canonical per-object snapshot manifest with
  SHA-256 checksums, restore from snapshot bytes into a fresh bucket, and four
  parity verifications (key set, content checksums, metadata, and
  database-reference consistency across `artifacts.storage_key` and
  `document_versions.content_ref`). Ledger emission is redacted and
  canonically checksummed; offline validation rejects failed checks,
  forbidden keys, credential-shaped values, tampering, and object-count
  shortfalls.
- Added `Settings.object_store_backend` (`OBSION_OBJECT_STORE_BACKEND`):
  `auto` preserves the historical behaviour; `memory`/`minio` force a backend
  so drills exercise the real S3-compatible write path without flipping
  environment-wide test posture. No existing environment changes.
- Added `obsion record-artifact-drill-evidence` and
  `make record-artifact-drill-evidence`. Both fail closed (exit 2) without
  `OBSION_DR_DRILL=1`; the Make target also requires docker. `.env.example`
  documents the backend setting and the drill opt-in.
- Restructured the candidate contract's `drillEvidence` section into a
  `ladders` list; `validate_release_candidate` dispatches per ladder kind and
  validates both drill families offline. The summary reports
  `drill_evidence_ledgers: 2` and `drill_evidence_checks: 16`; promotion
  eligibility is untouched and the `backup-restore-drill` operator gate
  remains PENDING.
- Recorded one real artifact drill ledger (8/8 checks passed).

## Architecture decisions

ADR 0065 records the core decisions: artifact-store readiness is an executed,
recorded drill rather than prose; seeding goes through the production REST API
into a real MinIO bucket so snapshotted bytes exercise real write paths;
restore reads from snapshot bytes on disk, never the live source; the binding
invariant is cross-store consistency between database storage references and
restored objects; a skip is never a pass; drill credentials never leave
process memory; and recorded drill evidence never feeds
`promotion_eligible`.

No runtime path changed: the one Python control plane, one App Server, durable
Harness hierarchy, Capability Gateway, Policy, Evidence, and credential
boundaries remain unchanged. The only configuration delta is the additive
`object_store_backend` setting whose default preserves existing behaviour.

## Migration

None. `database: none` in the release notes; `make migration-check` remains
part of the phase gate and confirms no Alembic drift.

## Validation

- `test_phase86_artifact_drill_evidence.py` (28 tests) covers the ladder
  contract and image pinning, opt-in gating, happy-path classification and
  checksum, credential redaction, fail-closed cascades (no docker, PostgreSQL
  or MinIO container failure, migration failure, seeder failure, bucket
  shortfall, missing database references, snapshot error, restore error,
  key-set divergence, checksum divergence, metadata divergence, database
  checksum divergence, missing database-referenced object),
  tamper/forbidden/shortfall detection, the real recorded ledger,
  candidate-gate binding across both ladders, duplicate-contract rejection,
  fail-closed Make target and CLI, backend selection, release notes, and
  project status.
- Historical Phase 82, 83, 84, and 85 suites continue to pass with the CLI
  default release manifest now `0.86.0-dev` and the ladder-list drillEvidence
  structure.
- `make check` covers Ruff formatting/lint, strict mypy, contract/Event/
  evaluation/release validation including the restructured `drillEvidence`
  section, secret scanning, all Python and frontend tests, and Alembic drift.
- Live drill with docker (self-cleaning, credentials never persisted): all
  eight checks passed.
- `obsion validate-release-candidate --contract-only` reports
  `drill_evidence_ledgers: 2`, `drill_evidence_checks: 16`,
  `promotion_eligible: false`, and the six pending operator gates.

## Remaining operator gates

- Clean staging/UAT, staging-scoped timed PostgreSQL/object-store restore
  with measured RPO/RTO, registry HIGH/CRITICAL CVE policy and signatures,
  live OIDC/secret manager/read replicas, security/data-owner approval, and
  maintainer-authorized publication remain `PENDING`; recorded drill evidence
  is readiness input, not promotion authority.
- The evidenced object-storage scope is single-bucket, single-region MinIO;
  cross-region replication, object-lock/versioning policies, and
  provider-specific IAM remain operator-owned.
- Ledgers are point-in-time audit records at one revision; refreshing them is
  an explicit operator action via `make record-drill-evidence` and
  `make record-artifact-drill-evidence`.
