# PHASE-78-REPORT — Vendor Knowledge read Gateway unification

## What was implemented

- Added versioned `knowledge.source.containers` and `knowledge.source.items`
  Capability contracts with canonical vendor-neutral container/item envelopes.
- Declared both contracts L1 and `SideEffect.NONE`, while retaining
  `knowledge.write` to preserve the existing source-management authorization
  boundary.
- Bound both Capability versions to the Feishu, DingTalk, WeCom, and Confluence
  connectors through source selectors.
- Routed all eight vendor browsing GET endpoints through the Phase 77 no-Run
  `CapabilityGateway.invoke_operator` entry.
- Removed REST-layer CredentialBroker and vendor Connector resolver access. REST now
  only maps canonical Gateway output to the existing response models.
- Extended the HTTP executor with budget-bounded space/workspace/node/page browsing
  for all four vendors.
- Persisted the selected CapabilityVersion on operator PolicyDecision fingerprints and
  Audit records without creating Run Events or Evidence.
- Added an explicit, non-writing Feishu live Gateway probe and operator command. It is
  separate from the historical Phase 76 three-probe adapter contract.
- Added ADR 0057, the Phase 78 architecture gate, operator guidance, system design,
  roadmap, README, and changelog updates.

## Architecture decisions

Vendor-specific nouns remain connector implementation details. The stable Capability
surface is containers plus items, selected by the binding's `source`. The REST API
preserves every existing vendor path and field name, but no longer owns execution,
credentials, or authorization.

The operator Gateway admits only the two exact L1/no-side-effect browse contracts and
the two exact Phase 77 L2/idempotent-write contracts. Policy, grants, schemas, rate,
credential resolution, timeout, masking, telemetry, and Audit therefore remain one
closed boundary. Operator browse output is source inventory, not Run Evidence; a
future Agent Run creates Evidence only through its own authorized retrieval path.

## Migration

No Alembic, Event, REST, Agent, Skill, model, or object-storage migration is required.
Builtin seeding publishes two new immutable Capability definitions/versions and eight
bindings. Existing Capability versions and Run pins remain replayable.

## Validation

- Vendor Knowledge focused regression — 61 passed, 3 documented live skips.
- Phase 78 offline governance — 5 passed; the separate live probe is skipped by
  default.
- Real Feishu no-write Gateway probe — 1 passed, 814 deselected.
- API/Registry/Event/Error contract suite — 29 passed.
- Full default Python suite — 792 passed, 22 documented opt-in/live skips before the
  separately marked Phase 78 live probe was added; its final offline target passed
  independently.
- Desktop, IDE, and TypeScript SDK suites — 50 passed.
- Ruff formatting/lint, strict mypy over 201 source files, contracts, evaluations,
  release-note validation, dataset gates, secret scan, frontend lint/typecheck, and
  Alembic drift check passed.
- PostgreSQL opt-in integration — 15 passed, with 3 destructive historical migration
  cases behind dedicated switches.

## Remaining risks

- Live permitted-document ingest/search/citation still needs a tenant-approved
  document id and ACL; authentication and safe browse/denial do not substitute for
  that data-owner validation.
- DingTalk, WeCom, and Confluence real-tenant browsing remains operator-owned.
- The Phase 77 no-Run L2 write path uses request correlation but has no durable
  principal-scoped idempotency replay/conflict ledger. A retried ingest may still
  create another DocumentVersion; Phase 79 must close this before claiming complete
  idempotent-write semantics.
- Staging, public DNS/TLS, live message delivery, and human security/data-owner
  sign-off remain operator-owned.
