# Phase 85 Alpha.1 backup/restore drill evidence architecture review

## Review question

Can PostgreSQL backup/restore readiness become executed, recorded,
machine-validated candidate evidence without touching deployed infrastructure,
weakening the operator-owned staging gate, or being mistaken for
production-promotion authority?

**Status: PASS for recorded repository-local drill evidence; PENDING for the
staging-scoped operator gate and external promotion.**

## Invariants reviewed

- The runtime architecture is unchanged: one Python control plane, one App
  Server, one Harness, Workspace → Thread → Turn → Run → Step → Event, and
  Capability Gateway → Policy → connector for every external access. The drill
  is release tooling plus one corrective migration; it adds no runtime path.
- The drill seeds through the real control-plane REST API
  (`_seed_drill_dataset` drives workspace creation, knowledge ingest, thread
  creation, and a governed Run to COMPLETED with Evidence and Claims). It does
  not hand-craft rows, so the dumped dataset exercises the production write
  paths that a real backup must preserve.
- PostgreSQL fidelity: the drill runs the pinned
  `pgvector/pgvector:0.8.6-pg17-bookworm` image (identical to docker-compose),
  migrates with the same Alembic invocation as `make migrate`, dumps with
  `pg_dump --format=custom`, and restores with `pg_restore --exit-on-error` —
  the same tooling the operator runbook prescribes.
- Fail-closed classification: once a stage fails, every downstream check is
  recorded `failed`; ledger validation rejects any failed check, so partial
  progress can never be committed as evidence. A skip is never a pass.
- Credentials never persist: the drill password and bearer token are generated
  per run (`secrets.token_urlsafe`), live only in process memory, and the
  ledger is scanned for credential material, forbidden keys, and
  credential-shaped URIs at record time and at validation time.
- Candidate-gate binding is promotion-neutral: `drillEvidence` validation
  reports ledger and check counts and never influences `promotion_eligible`;
  `backup-restore-drill` stays PENDING as an operator gate.

## Latent defect found by the drill

The first drill execution failed at `dataset-seeded`: every insert into
`verification_assessments` errored at COMMIT with `record "new" has no field
"assessment_id"`. Root cause: the deferred admission trigger resolved its
candidate id through one SQL CASE expression, and plpgsql validates the
`NEW.assessment_id` reference against the trigger row type when planning that
expression — which always fails on the parent table. Alembic revision
`b88f1c4d5e60` replaces the body with IF/ELSE branch statements so only the
executed branch is planned; verification rules are byte-identical, and a
downgrade/upgrade round trip was verified on a real container. This defect
predates Phase 85 and was invisible to the sqlite-backed default suite; it is
exactly the class of issue the drill exists to catch.

## Recorded drill results (2026-08-31, revision d4c6650)

`docs/release/evidence/alpha1/backup-restore-drill.yaml` records all eight
checks `passed`:

- source migrated to Alembic head `b88f1c4d5e60`; 10 threshold tables seeded
- custom-format dump of 458,132 bytes (SHA-256 recorded) restored cleanly
- schema-version parity, 89-table row-count parity, zero orphaned rows, and
  identical ordered audit identities after restore
- timings: migrate 1.7s, seed 2.3s, dump 0.2s, restore 0.5s, verify 15.0s,
  total 27.5s on the recording host

The ledger validates offline through `validate-release-candidate
--contract-only`, which reports `drill_evidence_ledgers: 1` and
`drill_evidence_checks: 8` with `promotion_eligible: false` and six PENDING
operator gates.
