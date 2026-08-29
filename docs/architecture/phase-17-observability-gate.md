# Phase 17 read-only observability connector review

## Review question

Can the first observability slice query metrics, logs, and deployment records through
the existing governed Capability Gateway, normalize provider responses into one
`ObservabilityEvent` subset, and persist the result as auditable Evidence without
opening a write or trace-dashboard path?

**Status: PENDING — automated checks do not constitute observability-platform or
security approval.**

## Delivery contract

- The bounded capability surface is `metric.query`, `metric.compare`, `metric.anomaly`,
  `log.search`, `log.aggregate`, and `deployment.list`. Requests carry an explicit
  operation, service, UTC time window, and bounded query fields.
- Connectors opt into `observability.v1` and remain ordinary HTTP bindings. The
  Gateway still resolves identity, Policy/Risk, connector grants, schema, rate limit,
  credential, timeout, masking, audit, telemetry, and Evidence before returning a
  result. No Agent receives an endpoint credential.
- Provider responses are reduced to `{operation, events, count, next_cursor}`. Every
  event contains the stable timestamp/service/environment/trace/request/business-id/
  deployment/commit/host/pod/severity subset; operation-specific values are confined
  to an allowlisted, redacted `attributes` map.
- Upstream errors, timeout, malformed JSON, provider error payloads, and unsupported
  operations return stable structured errors. Successful responses become `METRIC`,
  `LOG`, or `DEPLOYMENT` Evidence through the existing Gateway EvidenceFabric.

## Automated acceptance map

- `test_phase17_observability.py` covers Prometheus matrix normalization, event-field
  stability, HTTP request bounds, provider error handling, and secret omission.
- `test_phase8_capability_registry.py` and registry validation cover descriptor schemas,
  transport, Evidence mappings, and Planner registration filtering. Existing Gateway,
  Policy, Evidence, Audit, OpenAPI, SDK, PostgreSQL, Compose, Helm, and static contract
  gates remain required.

## Executed gate evidence

The final automated gate run reports 359 Python tests passed with 18 opt-in PostgreSQL
tests skipped; Ruff and mypy pass. Contract validation reports 258 error codes and 92
event versions. The static registry reports 8 agents, 4 skills, and 4 connectors.
Evaluation validation reports 28 cases across 3 datasets (23 RUN_OUTPUT, 3 ROUTING,
and 2 SQL_POLICY). Root package lint, typecheck, tests, and build pass; Compose
configuration and pinned Helm lint/template checks pass. A fresh PostgreSQL 17 instance
with pgvector 0.8.6 upgrades through the complete Alembic chain, reports no drift, and
passes the integration suite with 15 tests passed and 3 destructive migration tests
skipped by their explicit opt-in flags. Human approval remains pending regardless of
automation.

## Human review checklist

- Confirm the three provider bindings and their service/environment ACLs, egress hosts,
  credentials, rate limits, and time-window semantics.
- Confirm Prometheus/log/deployment field mappings, identifier hashing, severity policy,
  empty-result behavior, and Evidence classification/retention.
- Confirm that trace search, Kubernetes restart, deployment mutation, and all other
  write or dashboard operations remain outside the approved Phase 17 boundary.
