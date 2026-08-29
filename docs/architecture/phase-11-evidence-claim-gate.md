# Phase 11 Evidence Fabric and Claim review

## Review question

The human gate asks whether every factual result now uses one normalized, redacted
Evidence contract and whether Claim verification is strong enough for the Knowledge,
Data, and Incident scenarios. Automated completion does not create a human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Evidence contract

`EvidenceFabric` is the single normalization boundary for document, data/SQL, log,
Git, deployment, and tool observations. It requires a non-empty source/resource,
JSON-object content, finite confidence in `[0, 1]`, classification, permissions,
observed/ingested timestamps, and lineage. Content and lineage are recursively
redacted before a deterministic SHA-256 fingerprint is calculated. Permission labels
are normalized and deduplicated.

Capability Gateway output and workspace attachments both persist through this Fabric.
Replay copies immutable normalized rows with source lineage and never re-enters the
Fabric or any external connector.

## Claim contract

Claims are Run-scoped atomic statements linked by `ClaimEvidence`. Critic verification
requires the planned Evidence types and valid current-Run Evidence IDs; missing or
empty Evidence, unsupported Claim links, duplicate observations, or unresolved
conflicts prevent VERIFIED/high-confidence output. Only an explicitly non-factual
conversation path may complete without a Claim or Evidence.

Run inspection APIs expose safe Evidence and Claim projections. The Workbench opens
the cited Evidence directly from a Claim, preserving source, resource, observation
time, confidence, classification, and lineage.

## Automated acceptance map

- `test_phase11_evidence_fabric.py` covers source/resource trimming, key-aware
  redaction, deterministic fingerprinting, permission normalization, and confidence
  boundaries.
- `test_critic.py`, Harness/API tests, and run inspection tests cover no-Evidence
  high-confidence rejection, Claim↔Evidence links, Replay lineage, and tenant scope.
- Workbench static/browser contracts cover direct conclusion-to-Evidence navigation.
- Full Phase 1–10 contract, Policy/Gateway, Audit/Replay, OpenAPI, SDK, frontend,
  PostgreSQL, Compose, and Helm gates remain required.

## Executed gate evidence

- Phase 11 targeted Evidence Fabric, Critic, Gateway, Harness, and API tests passed
  (23 tests including the continuing policy and contract checks).
- Full Python suite: 344 passed, 18 opt-in PostgreSQL tests skipped by default.
- PostgreSQL integration suite, Alembic upgrade/check, SDK, frontend, Compose, and
  Helm verification are rerun as release gates after the current Phase 11 changes.

## Human review checklist

- Confirm that every production Evidence producer uses the Fabric and that Replay
  preserves normalized content/fingerprints without external re-execution.
- Confirm that Claim verification cannot be bypassed by an Agent, connector, or UI.
- Confirm that redaction and classification policies are sufficient for the real data
  sources and that tenant isolation remains intact.
