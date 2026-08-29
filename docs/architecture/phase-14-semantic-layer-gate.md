# Phase 14 semantic catalog and stable compiler review

## Review question

The human gate asks whether the semantic catalog is authoritative enough to be the
only source for DataAgent logical plans, without letting a model invent schema or SQL.
Automated completion does not create business-owner approval of a metric definition.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Catalog contract

Organization-scoped administration now supports versioned Metric, Dimension, Entity,
BusinessRule, TimeDefinition, and Synonym records, plus validated Entity relations.
Synonyms cannot point across tenants or to a missing target. “付费人数” is a registered
synonym for a stable `paid_user_count` Metric, not an instruction to invent a query.

## Compiler contract

Understanding resolves only validated, organization-owned metrics and dimensions. A
Logical Query carries IDs and bounded filters; the compiler loads the matching read-only
DataSource/Table/Column catalog, preserves requested dimension order, sorts definition
filters, binds parameters, and emits deterministic SQL. Unknown metrics, duplicate or
cross-table dimensions, unknown columns, and unsafe operators fail before query
execution. The generated SQL still passes AST policy and the Capability Gateway.

## Automated acceptance map

- `test_phase14_semantic_layer.py` covers catalog writes, version increments, relation
  ownership, synonym resolution, stable “paid users” SQL, and unregistered metric
  rejection.
- Existing Data catalog, SQL policy, Harness, Evidence/Claim, tenant isolation,
  PostgreSQL, SDK, frontend, OpenAPI, Compose, and Helm gates remain required.

## Executed gate evidence

- Phase 14 targeted semantic-layer tests passed (2 tests).
- Contract and static producer checks passed after updating the reviewed error map for
  the new catalog validation branches.
- The full release suite is rerun after this phase and recorded in the handoff.

## Human review checklist

- Confirm every Metric/Dimension/Entity/Rule/TimeDefinition has an accountable owner and
  a publication/version policy.
- Confirm expression and filter semantics against the source warehouse, including
  timezone, fiscal calendar, null handling, and distinct-count behavior.
- Confirm synonyms and relationships are reviewed for ambiguity and cannot widen tenant
  or row-level access.
- Confirm deterministic SQL is semantically equivalent to the approved logical plan and
  remains read-only, bounded, and policy validated.
