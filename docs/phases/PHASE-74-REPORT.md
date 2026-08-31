# PHASE-74-REPORT — Knowledge citation UI

## What was implemented

Phase 74 surfaces Vendor Knowledge citation provenance in the Workbench.

- `apps/web/src/lib/knowledge-citation.ts` formats provenance without inventing
  fields.
- `KnowledgeProvenance` renders source / connector / external id / revision /
  operation on Knowledge search results.
- Runtime Inspector DOCUMENT evidence shows citation provenance above raw JSON.
- ADR 0053 records that Experience UI reads Control Plane provenance contracts.

## Architecture decisions

UI never fabricates connector or revision values. Authorization remains on the
control plane; this phase is presentation only.

## Validation

- Recorded after the green suite run.

## Remaining risks

- Visual QA across dense inspector layouts remains operator-owned.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
