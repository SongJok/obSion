# Incident runbook (operating Obsion)

This is the operator procedure when Obsion itself is unhealthy. Product IncidentAgent
investigates tenant services through authorized read-only connectors; it does not
restart this control plane.

## Symptoms

- `/health/live` fails: process or runtime is down. Inspect Pod/container logs, then
  the previous Helm revision.
- `/health/ready` fails: PostgreSQL, Redis, or object storage is unavailable. The API
  must not skip readiness to recover traffic.
- Runs stuck in RUNNING: check Harness worker leases, `OBSION_RUN_TIMEOUT_SECONDS`,
  and connector circuit state (`capabilities_unavailable` after repeated HTTP failures).
- Evaluation gate failed: do not promote. Inspect immutable case results; bind real
  terminal Runs before re-running.
- Policy denials spike: review AuditLog. Do not grant `*` or disable Policy to restore
  a demo.

## Containment

Keep production write capabilities denied. Do not inject connector credentials into
Agent specs or model context. If a connector is abusive, disable the connector or
tighten egress; the circuit breaker already fail-closes that authority after repeated
transport errors.

## Recovery

1. Restore PostgreSQL from the last consistent backup if data is corrupt.
2. Roll Helm to the last known-good image digest.
3. Confirm `/health/ready`, then replay a Knowledge and a Data smoke Run.
4. File the incident with correlation IDs from `X-Request-ID` and Run IDs. Replay the
   failed Run instead of re-prompting production connectors when evidence already exists.
