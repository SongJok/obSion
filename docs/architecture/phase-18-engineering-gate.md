# Phase 18 read-only Git and change connector review

## Review question

Can the code/change slice connect commits to deployments through governed read-only
capabilities, return a bounded diff/history contract as CODE or DEPLOYMENT Evidence,
and fail closed for repositories outside the connector allowlist without exposing a
write path?

**Status: PENDING — automated checks do not constitute code-platform, delivery-platform,
or security approval.**

## Delivery contract

- The bounded operation surface is `git.commit`, `git.diff`, `git.history`,
  `deployment.commit`, and `code.search`. No auto PR, deployment mutation, restart,
  full Code Graph, or broad AST service is introduced.
- Connectors opt into `engineering.v1` and remain ordinary HTTP bindings. The Capability
  Gateway resolves identity, policy/risk, connector grants, schema, rate limit,
  credential, timeout, audit, telemetry, and Evidence before a provider request.
- Responses normalize to `{operation, items, count, next_cursor}` with stable timestamp,
  repository, commit/deployment, service/environment, author hash, title/status, and
  redacted bounded attributes. Patch/message/file values are allowlisted and length
  limited.
- Repository allowlists are checked before network access. Provider errors, timeouts,
  malformed payloads, unsupported operations, and repository violations retain stable
  structured error codes; successful CODE/DEPLOYMENT outputs become Evidence.

## Automated acceptance map

- `test_phase18_engineering.py` covers Git diff lineage, Deployment commit lineage,
  patch redaction, operation boundaries, and repository allowlists.
- Registry and Planner tests cover operation metadata and schema filtering. Existing
  Gateway, Policy, Evidence, Audit, OpenAPI, SDK, PostgreSQL, Compose, Helm, and static
  contract gates remain required.

## Executed gate evidence

The final automated gate run reports 362 Python tests passed with 18 opt-in PostgreSQL
tests skipped; Ruff and mypy pass. Contract validation reports 262 error codes and 92
event versions. The static registry reports 8 agents, 4 skills, and 4 connectors.
Evaluation validation reports 28 cases across 3 datasets (23 RUN_OUTPUT, 3 ROUTING,
and 2 SQL_POLICY). Root package lint, typecheck, tests, and build pass; Compose
configuration and pinned Helm lint/template checks pass. A fresh PostgreSQL 17 instance
with pgvector 0.8.6 upgrades through the complete Alembic chain, reports no drift, and
passes the integration suite with 15 tests passed and 3 destructive migration tests
skipped by their explicit opt-in flags. Human approval remains pending regardless of
automation.

## Human review checklist

- Confirm repository/branch ACLs, provider identity scopes, deployment-to-commit mapping,
  diff retention/redaction, time-window semantics, quotas, egress hosts, and credentials.
- Confirm unauthorized repository requests produce the agreed DENY behavior before any
  provider request and that no auto-PR, deployment-write, restart, or broad code-graph
  operation is reachable.
