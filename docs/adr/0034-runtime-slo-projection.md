# ADR 0034: Runtime SLO is a PostgreSQL projection, not an OTel p95 SLA

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires continuous core metrics: TTFT, total latency, steps, model
latency, tool latency, tokens, cost, success rate, replan rate, approval rate,
user satisfaction, and evidence coverage. OTel already records histograms and
counters. Admin had cost by ModelCall operation and a feedback summary, but no
tenant-scoped rate ledger. Inventing p95 from those histograms inside the API
would pretend a signed SLA. Shipping Kafka or ClickHouse as the V1 source would
violate the control-plane contract.

## Decision

`RuntimeSloService.project` reads the current organization from PostgreSQL:

- Success rate is completed / terminal Runs (`COMPLETED`, `FAILED`, `CANCELLED`).
- Total latency is the arithmetic mean of `completed_at - started_at`.
- TTFT stays histogram-only (`obsion.run.ttft`); the projection marks it unavailable.
- Model latency averages `model_calls.latency_ms`.
- Tool latency averages Capability step wall time (`source: capability-steps`).
- Replan rate counts `plan.updated` events against terminal Runs.
- Approval rate is approved / (approved + rejected).
- Satisfaction reuses the durable feedback buckets.
- Evidence coverage averages `verification_assessments.coverage`.

`GET /api/v1/admin/slo` requires `audit.read`. Feedback writes increment
`obsion.run.satisfaction`. The Workbench labels the panel as a PostgreSQL
projection, not a p95 SLA.

This is not vendor IM HTTP and not a second metrics store.

## Consequences

Operators can inspect the same numbers the control plane can defend from
transactions. A later warehouse or percentile SLA must keep PostgreSQL as the
correctness source and must not silently replace these averages with invented
percentiles.
