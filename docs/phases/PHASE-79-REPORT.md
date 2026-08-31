# PHASE-79-REPORT — Operator Capability idempotency

## What was implemented

- Added the principal-scoped `operator_capability_invocations` ledger for exact
  no-Run L2 `IDEMPOTENT_WRITE` Capability calls.
- Split execution into a durable pre-execution claim transaction and an atomic
  Knowledge/Audit/terminal-result transaction. No fake Run, Step, Event, Evidence,
  Approval, or second runtime protocol was introduced.
- Bound each request UUID to organization, principal, Capability name/version,
  Connector, PolicyDecision, and a canonical SHA-256 input fingerprint without
  persisting raw input or credentials.
- Re-evaluated current Policy on exact retries, then replayed immutable completed or
  failed results without another rate slot, credential resolution, or connector call.
- Rejected mismatched request-key reuse and concurrent in-progress duplicates. An
  expired lease becomes `UNKNOWN` and is never automatically retried.
- Added PostgreSQL transition, terminal-immutability, identity, and retention guards,
  plus a content-free admin projection and Workbench reconciliation panel.
- Added Python, TypeScript, and Java SDK access to the metadata-only admin projection.
- Required UUID `X-Request-ID` values on vendor source operations so a generic safe
  correlation string cannot be silently replaced by an unreplayable ledger key.
- Added independent seven-day retention configuration across local settings, Compose,
  and Helm; it is not coupled to App Server command retry retention.

## Architecture decisions

ADR 0058 records the two-transaction ledger and UNKNOWN semantics. The ledger is a
control-plane correctness aggregate, not a Harness execution model. Policy and schema
validation precede the claim; the claim commits before any secret or connector work;
the terminal result and business transaction commit together. Exact replay remains
subject to current Policy even though it returns the original pinned result.

Side-effect-free L1 vendor browsing remains outside this write ledger and continues
to execute normally through the no-Run Capability Gateway. Production writes, generic
Agent writes, automatic UNKNOWN retry, and fabricated Evidence remain denied.

## Migration

Alembic revision `a79c4d2e8f10` creates the ledger, composite tenant/principal key,
Capability/Connector/Policy foreign keys, bounded status checks, indexes, and the
PostgreSQL mutation guard. Upgrade, downgrade, and re-upgrade were verified in the
isolated temporary database `obsion_phase79_migration`, which was removed afterward.
Autogenerate reports no drift.

## Validation

- `make check` passed: Ruff format/lint, strict mypy over 202 source files, Event/error/
  evaluation/release-note validation, dataset gates, secret scan (0 findings),
  frontend lint/typecheck, 804 Python tests passed with 25 documented opt-in/live
  skips, 50 Desktop/IDE/TypeScript SDK tests passed, and Alembic reported no drift.
- Phase 79 API regression — 10 passed, covering terminal success/failure replay,
  generated and invalid request IDs, current-Policy reauthorization, rate/secret
  bypass, mismatch/in-progress conflicts, browse exclusion, UNKNOWN, Audit, and admin
  projection.
- PostgreSQL ledger concurrency/immutability/retention invariant — 1 passed.
- Isolated PostgreSQL migration upgrade/downgrade/re-upgrade — 1 passed.
- Java SDK under JDK 21 — 6 passed.
- Real non-writing Feishu Capability Gateway browse using operator-provided local
  credentials — 1 passed, 828 deselected. Credential values were never printed or
  persisted.

## Remaining risks

- UNKNOWN outcomes still require connector-specific operator reconciliation before a
  new request UUID is safe. This phase intentionally exposes no force-retry mutation.
- A connector/object-store write that escapes a rolled-back database transaction may
  still require orphan reconciliation; the ledger prevents blind duplicate execution
  but is not a distributed transaction coordinator.
- Live permitted-document ingest/citation, live message delivery, public DNS/TLS,
  DingTalk/WeCom tenants, staging, and human security/data-owner sign-off remain
  operator-owned.
