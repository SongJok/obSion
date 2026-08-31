# Phase 55 Runtime SLO review

## Review question

Does the control plane project goal.txt core rates from PostgreSQL, keep TTFT
honest as histogram-only, and refuse to present OTel histograms as a p95 SLA?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `GET /api/v1/admin/slo` requires `audit.read` and returns `source: postgresql`.
- Success, replan, approval, satisfaction, and evidence-coverage rates use durable
  rows. Empty denominators are `null`.
- Total / model / tool latency are arithmetic means. TTFT is marked
  `histogram-only`.
- Feedback writes increment `obsion.run.satisfaction`.
- Workbench discloses that the panel is not an OTel p95 SLA.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_phase55_runtime_slo.py` covers empty and completed projections, tenant
  isolation, authorization, and AST/UI bans on p95 and a second truth store.

## Human review checklist

- Confirm operators do not quote these averages as a signed production SLA.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
