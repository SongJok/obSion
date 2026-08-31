# ADR 0053: Workbench surfaces Knowledge citation provenance

- Status: Accepted
- Date: 2026-08-30

## Context

Phase 73 put `external_id`, `revision_id`, `connector_name`, and `operation` on
SearchHit and citation payloads. The Workbench Knowledge view and Runtime
Inspector still rendered only source/version, so operators could not inspect
connector provenance without reading raw JSON.

## Decision

Workbench Experience surfaces provenance without inventing fields:

- Knowledge search results render a `KnowledgeProvenance` block from SearchHit;
- Runtime Inspector DOCUMENT evidence shows citation provenance extracted from
  Evidence content hits;
- Missing fields stay empty / explicit “未记录”, never fabricated.

This is Experience UI over Control Plane contracts. It is not a marketplace and
does not change Harness/Policy/Capability authorization.

## Consequences

Operators can verify vendor Knowledge citations in the Workbench. Live tenant
data and design polish remain operator-owned.
