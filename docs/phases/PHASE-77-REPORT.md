# PHASE-77-REPORT — Vendor Knowledge write Gateway unification

## What was implemented

- Added `OperatorGatewayRequest` and a no-Run `CapabilityGateway.invoke_operator`
  entry.
- Unified eight Feishu/DingTalk/WeCom/Confluence REST ingest/sync endpoints on the
  capability registry, binding selector, Policy, connector grants, schemas, rate
  limiter, credential broker, HTTP executor, timeout, masking, telemetry, and Audit.
- Closed the entry to exact L2 idempotent `knowledge.ingest` / `knowledge.sync`
  contracts. Policy ASK fails closed without creating an Approval.
- Preserved vendor REST paths, payloads, response views, explicit ACL, and
  source-specific error behavior.
- Generalized capability schemas across all four vendors and added immutable
  `version_id` to ingest output lineage.
- Added nested database rollback around operator connector execution while retaining
  the outer Policy/Audit transaction.
- Expanded the reviewed static error producer/forwarding/helper manifests without
  weakening any analyzer.
- ADR 0056 records why operator writes cannot reuse a fabricated Run.

## Architecture decisions

Run-scoped Gateway behavior remains unchanged and generic Agent invocation remains
read-only. Operator source management is a distinct entry in the same Gateway class,
uses a user actor and request correlation id, and deliberately produces no Run Event
or Evidence. The ingested DocumentVersion is Organization Knowledge; Evidence is
created only when a real Run later retrieves or uses it.

## Migration

No Alembic or Event migration is required. Capability schema checksum changes create
new immutable builtin versions on seed; previous Run pins remain valid.

## Validation

- Vendor write focused suite — 56 passed, 3 documented live skips.
- Phase 77 governance suite — 5 passed.
- API/registry/contract/provenance suite — 32 passed.
- `make check` — passed: Ruff format/lint, strict mypy over 200 source files,
  contracts/evaluations/release notes/secret scan, frontend lint/typecheck, 787
  Python tests passed with 22 documented opt-in/live skips, 50 Desktop/IDE/
  TypeScript SDK tests passed, and Alembic reported no drift.
- PostgreSQL opt-in integration — 15 passed, with 2 historical destructive migration
  cases behind dedicated switches.

## Remaining risks

- Vendor browsing GET routes are not yet on the operator Gateway and are explicitly
  the next phase.
- Operator writes do not create Run Evidence by design; operators inspect Document
  provenance and Audit, while Agents create Evidence during a real Run.
- A connector that writes object storage before a later database failure can still
  require object-store orphan reconciliation; database changes are savepoint-rolled
  back and the failure is audited.
- Live permitted-document ingest, staging, and tenant data-owner/security sign-off
  remain operator-owned.
