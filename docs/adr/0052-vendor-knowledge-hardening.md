# ADR 0052: Shared Vendor Knowledge hardening contracts

- Status: Accepted
- Date: 2026-08-30

## Context

Feishu, DingTalk, WeCom, and Confluence Knowledge connectors each enforced
ad-hoc walk limits and provenance metadata. DingTalk and WeCom silently truncated
sync walks. Operator REST ingest bypassed Capability Gateway rate limiting.
Search hits and citations omitted connector/external revision provenance.

## Decision

Introduce `obsion.knowledge.connector_contract`:

- `KnowledgeConnectorBudget` / `SyncBudgetTracker` — shared pages/nodes/depth
  budget; exhaustion raises `knowledge_sync_budget_exceeded` (fail-closed);
- `VendorKnowledgeProvenance` — unified version metadata and sync envelopes;
- `enforce_knowledge_capability_rate_limit` — Operator REST uses the same
  rate-limit key semantics as Capability Gateway (`capability_rate_limited`).

All four vendor clients consume the shared tracker. Sync responses always
include `budget` and `provenance`. Search hits and citations carry
`external_id`, `revision_id`, `connector_name`, and `operation` when known.

## Consequences

Operators can see incomplete syncs as budget errors instead of silent success.
REST and Harness share one safety throttle. Citation provenance is inspectable
without inventing marketplace, Kafka, or a second control-plane language.
