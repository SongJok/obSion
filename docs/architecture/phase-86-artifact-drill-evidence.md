# Phase 86 Alpha.1 artifact-store drill evidence architecture review

## Review question

Can the object-storage half of the recovery story — "PostgreSQL plus artifact
bytes" — become executed, recorded, machine-validated candidate evidence
without touching deployed infrastructure, weakening the operator-owned staging
gate, changing the runtime architecture, or being mistaken for
production-promotion authority?

**Status: PASS for recorded repository-local drill evidence; PENDING for the
staging-scoped operator gate and external promotion.**

## Invariants reviewed

- The runtime architecture is unchanged: one Python control plane, one App
  Server, one Harness, Workspace → Thread → Turn → Run → Step → Event, and
  Capability Gateway → Policy → connector for every external access. The drill
  is release tooling plus one additive configuration setting; it adds no
  runtime path.
- The drill seeds through the real control-plane REST API
  (`_seed_artifact_scenario` drives workspace creation, knowledge-document
  ingest, workspace file artifact upload, and a content roundtrip read). It
  does not hand-craft bucket objects, so the snapshotted dataset exercises the
  production `MinioObjectStore` write path that a real backup must preserve.
- Storage fidelity: the drill runs the pinned
  `quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z` and
  `pgvector/pgvector:0.8.6-pg17-bookworm` images (both identical to
  docker-compose), migrates with the same Alembic invocation as
  `make migrate`, snapshots every object with its content type and user
  metadata, and restores from snapshot bytes on disk — never from the live
  source — so the evidence proves backup-mediated recovery.
- The binding invariant is cross-store consistency: every
  `artifacts.storage_key` and `document_versions.content_ref` database row
  must resolve to a restored object whose SHA-256 matches the database
  checksum. This is the property a real disaster recovery must hold, and no
  single-store drill can prove it.
- Fail-closed classification: once a stage fails, every downstream check is
  recorded `failed`; ledger validation rejects any failed check, so partial
  progress can never be committed as evidence. A skip is never a pass.
- Credentials never persist: the PostgreSQL password, MinIO root credentials,
  and bearer token are generated per run (`secrets.token_urlsafe` /
  `secrets.token_hex`), live only in process memory, and the ledger is scanned
  for credential material, forbidden keys, and credential-shaped URIs at
  record time and at validation time. The ledger records counts, timings, and
  SHA-256 digests — never object keys, endpoints, or ports.
- Candidate-gate binding is promotion-neutral: the `drillEvidence` section is
  now a `ladders` list validated per ladder kind, reporting ledger and check
  counts only; it never influences `promotion_eligible`, and
  `backup-restore-drill` stays PENDING as an operator gate.

## Configuration addition

`Settings.object_store_backend` (`OBSION_OBJECT_STORE_BACKEND`) is `auto`
(default), `memory`, or `minio`. `auto` preserves the historical behaviour —
in-memory under `TEST`, MinIO otherwise — so no existing environment changes.
The explicit values exist so integration drills and operator test rigs can
exercise the real S3-compatible backend without flipping environment-wide
posture (rate limiter, auth, model gateway) off `TEST`. The selection logic is
a single pure helper (`_uses_memory_object_store`) with unit coverage.

## Scope boundary

The evidenced scope is single-bucket, single-region object storage on the
pinned MinIO image. Cross-region replication, object-lock/versioning policies,
provider-specific IAM, and the staging-scoped timed restore with measured
RPO/RTO remain operator-owned; the staged gate keeps its PENDING status and
recorded drill evidence is readiness input, not promotion authority.
