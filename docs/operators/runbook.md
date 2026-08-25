# Operator runbook

## Production prerequisites

Obsion production requires PostgreSQL 17 with pgvector 0.8 or a compatible managed
service with the `vector` extension enabled, Redis with authentication and persistence,
S3-compatible object storage, an OIDC provider, TLS
termination, a secret manager, and an OTLP/HTTP collector. Query connectors must use a
separate read-only identity and a replica or governed query service.

Set `OBSION_ENVIRONMENT=production` and `OBSION_AUTH_MODE=oidc`. Startup fails when the
issuer, audience, or JWKS URL is missing, and production cannot fall back to the seeded
development identity. Keep database, connector, model, and object-store secrets out of
Helm values and inject them through Kubernetes Secrets backed by the organization's
secret manager.

Set `OBSION_KNOWLEDGE_EMBEDDING_PROFILE` to a logical model profile whose bound
OpenAI-compatible endpoint declares the `embeddings` capability, supports the document
classification, and returns 1,536-dimensional vectors. If it is unset, the authorized
PostgreSQL full-text path remains available; production semantic retrieval should
configure it before ingesting the corpus. Retrieval ACL predicates execute in SQL
before either full-text or HNSW ranking.

Automation is enabled by default. Size `OBSION_AUTOMATION_WORKER_CONCURRENCY` together
with the Harness worker pool and database capacity. Keep
`OBSION_AUTOMATION_LEASE_SECONDS` comfortably above the polling interval. Every API
replica may schedule and execute workflows safely because claims use PostgreSQL row
locks and leases; do not run a separate privileged scheduler identity.

The governed Action worker is enabled with `OBSION_ACTIONS_ENABLED=true`. Size
`OBSION_ACTION_WORKER_CONCURRENCY` against provider and database capacity, keep
`OBSION_ACTION_LEASE_SECONDS` above the polling interval, and use the shared
fail-closed distributed rate limiter. Setting the flag to `false` pauses claims; it
does not relax policy or turn an approved request into a success.

Register action providers only as active HTTP capability connectors with explicit
permission grants, environment, resource selector, and exact egress authority. Use
TLS outside development and store bearer credentials as secret references. V1 binds
only `action.pr.create/close` and `action.ticket.create/close` in development or
staging. Production targets and config/restart/deploy capabilities cannot be enabled
through roles, policies, connector configuration, or environment variables.

## Install and upgrade

1. Back up PostgreSQL and verify the last restore test.
2. Review `CHANGELOG.md`, migration SQL, policy changes, and Agent/Skill checksums.
3. Build or pull images by immutable digest and scan/sign them in the deployment
   registry.
4. Run `alembic upgrade head` once as a deployment job. The Helm chart creates a
   pre-install/pre-upgrade migration Job and blocks rollout if it fails.
5. Roll out the API, then the Workbench. Readiness must pass before traffic shifts.
6. Verify a knowledge run, an authorization denial, evidence/claims, audit search, an
   event-stream reconnect, and a manual notification-only automation run. If a staging
   action provider is configured, verify preflight with a non-destructive test target,
   independent approval, provider idempotency, separate rollback approval, and both
   action audit records.

Schema downgrades are an incident procedure, not an automatic rollback. If a migration
is not backward compatible, restore the backup into a new database and point the prior
application version at it after validating tenant and audit integrity.

## Health and telemetry

- `GET /health/live` proves the process event loop is responsive.
- `GET /health/ready` proves the application can query PostgreSQL.
- HTTP, SQLAlchemy, Harness, capability, policy, and model spans export through
  OTLP/HTTP when `OBSION_OTEL_EXPORTER_OTLP_ENDPOINT` is configured.
- Counters include `obsion.runs`, `obsion.capability.invocations`,
  `obsion.policy.decisions`, `obsion.model.calls`, and
  `obsion.automation.executions`; governed attempts add `obsion.action.attempts` by
  action type, purpose, and outcome.

Alert on readiness failure, run failure/cancellation rate, worker lease age, capability
timeouts, policy/rate-limit safety-service failure, approval backlog/expiry, model
latency and cost, database pool saturation, Redis errors, and missing telemetry.
Also alert on overdue enabled schedules, expired automation leases, repeated workflow
failure codes, invalid owners, and human-review backlog age.
Alert separately on old action approvals, expired action leases, provider timeout or
failure rate, rollback backlog/failure, policy denial spikes, and any attempted
production or deferred action.
Telemetry and exception messages must be sampled for secret-redaction regressions.

## Backup and recovery

Back up PostgreSQL with point-in-time recovery and encrypt backups with a separately
controlled key. Version and replicate the artifact bucket; include retention rules for
classification and legal hold. Redis is coordination state and may be rebuilt, but
persistence reduces rate-limit discontinuity during restart.

Quarterly restore tests must verify organizations, memberships, runs, ordered events,
policy decisions, approvals, audits, evidence, claims, registry versions, semantic
objects, document versions, workflow versions, schedules, automation executions,
review decisions, action requests, immutable action plans, execute/rollback approvals,
attempts, policy decisions, and notification deliveries. Restore tests must confirm
that the action-plan mutation trigger is present. Never restore production data into
an environment with development authentication.

## Incident procedures

### Database unavailable

Remove unhealthy API pods from traffic, pause upgrades, restore connectivity or fail
over to the validated replica, and confirm ordered event writes before re-enabling run
workers. Do not manually mark partially executed runs complete.

### Redis unavailable

With `OBSION_RATE_LIMIT_FAIL_CLOSED=true`, new capability executions fail safely while
workspace reads continue. Restore Redis, confirm authentication and key expiry, then
replay failed runs explicitly; do not disable fail-closed behavior in production.

### Connector or model provider degraded

Disable the endpoint or connector binding in the administration plane. Existing runs
retain their pinned versions and failure events. Enable a model fallback only when its
classification, region, tool-use, and budget policy are equivalent.

### Action provider response lost or degraded

First disable the affected action connector binding to stop new preflights and claims,
then inspect the request's sealed plan, attempts, policy decisions, events, and
provider idempotency ledger. Do not edit an `ActionPlan`, change an attempt key, or
manually mark lifecycle rows complete. An expired lease is reclaimed automatically
with the same provider key, so a compliant provider returns the original result
without repeating the write.

If Obsion records `FAILED` but the external write may have committed, restore the
pinned provider and request rollback through the normal endpoint. Rollback requires a
new independent approval. If the original pinned connector cannot be restored, perform
the external remediation under the organization's break-glass process and preserve
both systems' evidence; creating a new binding does not rewrite an existing sealed
plan.

### Suspected data exposure

Disable the relevant connector binding and model endpoint, preserve audit/event data,
rotate credentials, identify affected principals/resources by correlation ID, and
follow `SECURITY.md`. Do not delete evidence or audit records during investigation.

### Automation schedules overdue

Confirm API readiness and PostgreSQL write availability, then inspect enabled schedules,
their `next_fire_at`, `last_error_code`, and current owner permissions. A disabled
schedule with an ownership error must be reassigned or have permissions restored before
an authorized operator re-enables it. Never edit `next_fire_at` or insert an execution
manually; use a manual idempotent trigger when an occurrence must be replayed.

## Routine maintenance

- Review expired approvals and memory candidates weekly.
- Review pending action approvals, failed/rollback-failed actions, and provider
  idempotency retention weekly; retention must exceed the longest action recovery and
  audit window.
- Review policy denials, rate-limit changes, connector health, and cost anomalies daily.
- Re-run version-pinned evaluations before promoting agents, skills, prompts, models,
  semantic definitions, or capability schemas.
- Retire old versions only after retained runs no longer require replay against them.
