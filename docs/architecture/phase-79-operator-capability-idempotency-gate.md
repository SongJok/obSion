# Phase 79 Operator Capability idempotency review

## Review question

Can an authenticated no-Run L2 idempotent write be retried safely after response loss
without repeating execution, corrupting Harness state, exposing secrets, or silently
retrying an unknown outcome?

**Status: PENDING — automated checks do not constitute staging, tenant data-owner, or
security approval.**

## Delivery contract

- Only exact operator `SideEffect.IDEMPOTENT_WRITE` contracts enter the persistent
  ledger; L1 browse and Run-scoped Agent calls do not.
- The request key is organization/principal scoped and binds Capability name plus a
  SHA-256 fingerprint of payload, resource, environment, and context.
- Caller-supplied vendor source request IDs must be UUIDs; invalid generic
  correlation strings fail before any credential or connector work.
- Policy/grant/schema checks precede claim; the durable `IN_PROGRESS` claim commits
  before rate/credential/connector execution.
- Exact replay re-evaluates current Policy but bypasses rate, credentials, and the
  connector, then appends a `REPLAYED` Audit.
- Input mismatch is a stable 409 conflict. Concurrent duplicate work returns in
  progress and does not execute.
- Connector result, Knowledge transaction, Audit, and terminal ledger state commit
  atomically in the second transaction.
- Lost completion changes to UNKNOWN after lease expiry and is never auto-retried.
- PostgreSQL guards identity, terminal immutability, transition direction, and
  retention deletion.
- The admin/API/SDK/Workbench projection excludes payload and terminal result content.
- Python, TypeScript, and Java SDKs expose the same read-only reconciliation
  projection. Its retention window is independently configurable and defaults to
  seven days.
- No Run, Step, Event, Evidence, Approval, model context, or credential is created or
  persisted by the ledger.

## Automated acceptance map

- `test_phase79_operator_capability_idempotency.py` proves success/failure replay,
  server-generated keys, UUID fail-closed behavior, current-Policy reauthorization,
  rate/secret/connector bypass on replay, mismatch/in-progress conflict, browse
  exclusion, UNKNOWN handling, Audit, and content-free admin projection.
- `test_postgres_operator_invocations.py` proves concurrent claims, terminal replay,
  database immutability, and retention guards on PostgreSQL.
- `test_postgres_operator_invocation_migration.py` proves upgrade, downgrade, and
  re-upgrade in an isolated PostgreSQL database.
- Phase 77/78 and contract/security suites preserve Policy, Audit, source response,
  no-Run/no-Evidence, and Event-protocol boundaries.

## Migration review

Alembic revision `a79c4d2e8f10` creates the ledger, tenant/principal/capability/
connector/policy foreign keys, checks, indexes, and mutation guard. Upgrade,
downgrade, re-upgrade, autogenerate drift, and PostgreSQL invariant tests are required.

## Human review checklist

- Confirm retention and incident procedures for UNKNOWN outcomes.
- Confirm connector-specific reconciliation before issuing a new request UUID.
- Confirm database backup/restore retains ledger and Audit together.
- Validate staging process termination between claim, connector return, and terminal
  commit; automated transaction tests do not replace operational chaos testing.
