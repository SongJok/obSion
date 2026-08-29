# Phase 8 Capability Registry review

## Review question

The human gate asks whether capability identity, immutable versions, descriptor
schemas, Evidence output contracts, and Planner registration filtering are suitable
as the long-term Capability Registry baseline. Automated completion does not create a
human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Boundary

Phase 8 makes capabilities discoverable through a tenant-scoped registry. A Registry
descriptor is metadata and a contract; it is not an execution path. The first entries
are deliberate placeholders:

```text
knowledge.search  data.query  metric.query  log.search  git.diff
```

No placeholder implements a connector, SQL query, HTTP request, or production-side
effect. Execution remains a later Capability Gateway/Policy concern.

## Descriptor contract

Every active `CapabilityVersion` is projected through `CapabilityDescriptor` with:

- stable definition/version IDs and a monotonically meaningful version number;
- transport, display metadata, permission action, timeout, risk and side-effect class;
- Draft 2020-12 input and output JSON Schemas;
- data classification and an output contract whose kind is `Evidence` and whose mapping
  declares a non-empty Evidence type.

Invalid schemas or missing Evidence mapping fail with registered validation errors
before the descriptor is exposed. The API only returns active versions in the current
organization for which the Principal has the declared permission.

## Planner boundary

Before planning, Harness intersects the AgentSpec capability IDs with the current
organization's active registry and permission-visible set. A capability not in that
intersection cannot become a plan Step. This keeps the planner declarative and avoids
hard-coded capability selection while preserving the Phase 7 no-capability failure
semantics.

## Automated acceptance map

- `test_phase8_capability_registry.py` verifies the five placeholder descriptors,
  schema/Evidence/risk/side-effect/permission/timeout fields, detail-version parity,
  and planner filtering of unregistered capabilities.
- `test_contract_quality_gates.py` verifies that descriptor validation errors are
  registered and that every producer, helper call, and forwarding sink remains mapped
  to the machine-readable error catalog.
- The continuing Phase 1–7 contract, identity, App Server, streaming, Workbench,
  Model Gateway, Harness, OpenAPI, SDK, frontend, migration, Compose, and Helm gates
  remain required for Phase 8 acceptance.

## Executed gate evidence

- Phase 8 targeted and contract-quality tests passed: 7 tests.
- Full Python suite passed: 327 tests, with 18 opt-in PostgreSQL tests skipped in the
  default run.
- PostgreSQL integration suite passed against a disposable pgvector database: 15
  non-destructive tests; the three opt-in historical migration round-trip tests also
  passed independently on fresh disposable databases.
- The complete Alembic chain upgraded to head and `alembic check` reported no drift.
- Ruff lint/format, strict mypy, Event/Error/Registry/Evaluation validation, frontend
  lint/typecheck/build, TypeScript SDK tests, Docker Compose rendering, and Helm
  lint/template rendering all passed.

## Human review checklist

- Confirm that descriptor fields are sufficient for a later Gateway/Policy decision
  without embedding connector implementation details.
- Confirm that version pinning and organization/permission filtering are a stable
  discovery contract for Harness, Workbench, SDKs, and replay.
- Confirm that the five entries remain explicit placeholders until their real systems,
  ACLs, evidence normalization, and golden datasets are delivered in later phases.
