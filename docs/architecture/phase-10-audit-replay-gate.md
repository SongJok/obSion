# Phase 10 Audit, Trajectory, and Replay review

## Review question

The human gate asks whether the append-only audit dimensions, privacy redaction, and
read-only Run Replay are strong enough to support Evidence and real connector phases.
Automated completion does not create a human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Decision contract

Every governed result must be attributable through one tenant-scoped AuditLog row and
the immutable Run Event sequence:

```text
who / when / agent / model / capability / resource / policy / approval
                / result classification / risk / latency
```

Audit metadata is recursively redacted before persistence. Turn input is sanitized
before it can enter a durable Turn, Event, context snapshot, Audit, or model request.

## Replay contract

Replay accepts only a terminal source Run, takes a row lock on the active Replay Run,
and materializes a deterministic snapshot. Steps, evidence, claims, artifacts, memory
and conversation snapshots receive new IDs with explicit source lineage; source events
are wrapped as ordered `run.replay.event` facts. The source snapshot fingerprint is
stable across repeated Replay requests. No Model Gateway, Capability Gateway,
connector, network, or credential broker is called by Replay.

## Automated acceptance map

- `test_phase10_audit_replay.py` verifies secret-free Turn persistence and canonical
  completion AuditLog dimensions exposed through the tenant-scoped admin projection.
- `test_security.py` verifies assignment, Bearer, credential-URI, private-key-block, and
  recursive key redaction without mutating caller payloads.
- Existing API Replay tests verify full source event ordering, remapped lineage,
  evidence/claim/artifact/memory/conversation restoration, and stable fingerprints.
- Existing Event, static error, PostgreSQL, SDK, frontend, Compose, and Helm gates
  remain required for release acceptance.

## Executed gate evidence

- Phase 9 targeted tests: 9 passed.
- Phase 10 targeted tests: 2 passed; security redaction tests: 3 passed.
- Full Python suite: 339 passed, 18 opt-in PostgreSQL tests skipped by default.
- Ruff lint/format and strict mypy passed after the Phase 10 audit and redaction changes.
- Contract, Registry, Evaluation, OpenAPI, SDK, frontend, migration, Compose, and Helm
  verification are rerun as the release gate for the current working tree.

## Human review checklist

- Confirm that raw prompt credentials and sensitive metadata cannot enter any durable
  record or replay snapshot.
- Confirm that AuditLog dimensions are sufficient for incident accountability and that
  access is tenant-scoped and itself governed.
- Confirm that Replay is complete for the supported Run snapshot and cannot invoke an
  external model, connector, network, or credential path.
