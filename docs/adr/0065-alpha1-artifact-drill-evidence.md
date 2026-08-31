# ADR 0065: Alpha.1 artifact-store readiness is an executed, recorded drill

- Status: Accepted
- Date: 2026-09-01

## Context

The operator runbook defines the recovery story as "PostgreSQL plus artifact
bytes": the database is the transactional source of truth, and the artifact
bucket (`obsion-artifacts`) holds the knowledge-document and workspace-file
content that database rows reference by storage key and SHA-256. ADR 0064
recorded the PostgreSQL half of that story as an executed drill. The object
store remained documented prose, so the repository still had no automated proof
that the byte half survives a snapshot/restore round trip — and, critically, no
proof that every database-referenced object is actually recoverable, which is
the invariant a real disaster recovery must hold.

## Decision

Artifact-store readiness becomes a second declared ladder plus a recorded
ledger, sharing the Phase 85 machinery and invariants.

`docs/release/alpha1-artifact-drill-evidence-contract.yaml`
(`ArtifactDrillEvidenceLadder`) declares eight ordered checks, the pinned
`quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` and
`pgvector/pgvector:0.8.6-pg17-bookworm` images (both identical to
docker-compose), and a minimum object threshold.

`obsion record-artifact-drill-evidence` (module
`obsion.release.artifact_drill`) requires `OBSION_DR_DRILL=1` and docker. It
starts one throwaway PostgreSQL and two throwaway MinIO containers with per-run
generated credentials, migrates the database with Alembic, and seeds through
the real control-plane REST API: a knowledge-document ingest and a workspace
file artifact upload whose bytes land in the source bucket through the
production `MinioObjectStore` write path. The source bucket is snapshotted to a
local directory as payload files plus a canonical manifest (per-object size,
SHA-256, content type, and user metadata), the snapshot is restored from disk
into a fresh bucket on the second MinIO container, and the drill verifies
key-set parity, per-object content checksums, metadata preservation, and —
the binding invariant — that every `artifacts.storage_key` and
`document_versions.content_ref` database row resolves to a restored object
with a matching SHA-256. Once a stage fails, every downstream check is
recorded `failed` — a skip is never a pass.

The ledger (`ArtifactDrillEvidenceLedger`) records check classifications with
bounded content-free details, stage timings, object and byte counts, manifest
and key-set SHA-256 digests, and database reference counts — never object
keys, credentials, endpoints, or ports. Validation rejects failed checks,
forbidden keys, credential-shaped values, tampered checksums, and object-count
shortfalls.

Seeding the production write path under `TEST` requires one configuration
addition: `Settings.object_store_backend` (`OBSION_OBJECT_STORE_BACKEND`)
selects `auto` (default; in-memory under `TEST`, MinIO otherwise), `memory`,
or `minio`. The default changes no existing environment; the explicit values
let integration drills and operator test rigs exercise the real S3-compatible
backend without flipping environment-wide test posture.

The candidate contract's `drillEvidence` section becomes a `ladders` list so
each drill family binds its own contract and ledgers; the validator dispatches
on ladder kind. Recorded drill output remains readiness input only: it never
feeds `promotion_eligible`, and the staging-scoped `backup-restore-drill`
operator gate remains PENDING.

## Consequences

- The recovery story is now evidenced end to end at the repository level:
  PostgreSQL bytes (Phase 85) and artifact bytes (Phase 86) both survive
  restore, and database references resolve against the restored bucket.
- Restores read from snapshot bytes on disk, not from the live source, so the
  evidence proves backup-mediated recovery rather than a live copy.
- The drill is self-cleaning (three containers plus the snapshot directory are
  removed in every outcome) and safe on any docker-capable host.
- Single-bucket, single-region object storage is the evidenced scope;
  cross-region replication, object-lock/versioning policies, and
  provider-specific IAM remain operator-owned, as does the staging-scoped
  timed restore with measured RPO/RTO.
