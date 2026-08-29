# Phase 15 SQL Compiler, Validator, and Query Gateway review

## Review question

Can every governed analytics request be compiled from a Logical Query into bounded,
read-only SQL, checked as an AST, and executed only through a read replica without
exposing credentials or bypassing row/column controls?

**Status: PENDING — automated checks do not constitute production security approval.**

## Delivery contract

- `SqlPolicyValidator` parses one statement, permits SELECT/WITH and non-executing
  EXPLAIN, rejects mutations, dangerous functions, unknown tables/columns, wildcard
  projections, and EXPLAIN ANALYZE/BUFFERS.
- Explicit LIMIT mode rejects unbounded external SQL; semantic compilation retains a
  controlled default LIMIT and applies the lower of global/source limits.
- Query Gateway uses read-only PostgreSQL transactions, statement timeout plus an
  application deadline, a PostgreSQL EXPLAIN scan-budget preflight, row-policy predicates,
  and DataColumn mask/hash policies. Primary-source markers fail closed.
- `/data/sql/validate` exposes normalized policy facts. `/data/sql/explain` emits a
  deterministic policy plan and an auditable `audit_id`; neither endpoint returns a
  credential or SQL parameter value.

## Automated acceptance map

- `test_phase15_sql_gateway.py` covers no-LIMIT rejection, dangerous SQL interception,
  EXPLAIN safety, deterministic scan-budget rejection, and the auditable explain route.
- `test_sql_policy.py` preserves the governed compiler's default-limit compatibility;
  full contract, static producer, SDK, frontend, Compose, Helm, and PostgreSQL gates
  remain required.

## Executed gate evidence

Executed evidence: the full Python suite passed **354 tests with 18 opt-in skips**;
the Phase 15 SQL/contract targeted set passed; Ruff and strict mypy passed; Python SDK
passed 14 tests and TypeScript SDK passed 15 tests. Contract validation reports **255
error codes**, Event registry version 1 with 92 versions/events; registry validation
reports 8 agents, 4 skills, and 4 connectors; evaluation validation reports 27 cases
across 3 datasets (23 RUN_OUTPUT, 2 ROUTING, 2 SQL_POLICY). Root npm lint,
typecheck, test, and build passed, as did Compose config and pinned Helm lint/template.
A disposable PostgreSQL 17/pgvector instance upgraded to head, passed `alembic check`,
and passed the integration gate (**15 passed, 3 opt-in skips**). Human review remains
pending regardless of the automated result.

## Human review checklist

- Confirm the read-replica topology and Connector `role`/`read_only` provisioning source.
- Set scan-budget and timeout thresholds from production workload statistics.
- Review row-policy expression shape and DataColumn mask/hash policy owners.
- Confirm EXPLAIN permissions, audit retention, and parameter/credential redaction.
- Verify dangerous-SQL interception and no-LIMIT behavior against the production dialects.
