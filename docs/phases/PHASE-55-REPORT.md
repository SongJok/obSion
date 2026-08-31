# PHASE-55-REPORT — Runtime SLO projection

## What was implemented

Phase 55 projects goal.txt core runtime metrics from PostgreSQL.

- `RuntimeSloService` computes success, replan, approval, satisfaction, evidence
  coverage, tokens, cost, steps, and mean latencies for the caller organization.
- TTFT remains an OTel histogram (`obsion.run.ttft`) and is not invented as p95.
- `GET /api/v1/admin/slo` requires `audit.read`. Feedback writes increment
  `obsion.run.satisfaction`. Workbench shows the ledger as a PostgreSQL projection.
- Python and TypeScript SDKs expose `get_runtime_slo` / `getRuntimeSlo`.

## Architecture decisions

OTel counters were not a tenant admin ledger. Pretending histogram p95 is an SLA
would be a fake integration. ADR 0034 keeps PostgreSQL as the source and leaves
TTFT unavailable in the projection. Vendor IM HTTP remains blocked.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 627 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase55_runtime_slo.py`.
- TypeScript SDK: 22 passed.

## Remaining risks

- Character and wall-clock averages are not provider percentiles.
- Conversation Runs may have zero Capability steps and therefore empty tool
  latency.
- Vendor IM live HTTP, remote connector processes, and signed `1.0.0` remain
  blocked or operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
