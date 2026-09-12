# ADR 0120 — Serialize local source revocation with final publication

- Status: accepted as a development increment; normal-runtime and live document-answer acceptance remain pending.
- Date: 2026-09-12
- Builds on: ADR 0115–0119 and the existing durable DingTalk Outbox.

## Decision and ordering contract

A successful source check and a later publication must not straddle a committed local authorization change. Introduce an organization-scoped PostgreSQL transaction advisory fence. The final Harness publication check acquires a shared fence before reading current provenance, identity, Policy and source access, and holds it through the answer transaction. The claimed DingTalk Outbox acquires the same fence before loading dispatch inputs and holds it through the Gateway result and transaction completion. Model authoring and review do not acquire this fence.

Authorization-relevant INSERT, UPDATE and DELETE operations on the migration's fixed 23-table allowlist acquire the corresponding exclusive fence through database triggers. The allowlist covers source records, document versions and ACLs, identities and roles, Policy, connectors and version revocations, IM installations/bindings/audiences, workspace membership, capabilities and secret references. This includes direct SQL writes to those records. Operational-only updates explicitly listed in the migration may proceed. Future columns are protected by default. Connector configuration, grants and egress JSON also require serialized-text equality before an update is considered harmless: the source-access predicate deliberately uses that stricter representation, so JSONB equality alone was insufficient.

If publication acquires its fence first, revocation cannot commit until publication finishes or rolls back. If revocation acquires its fence first, publication waits and then checks the new committed state. Multiple publishers in the same organization share the fence; a different organization's authorization writes do not contend except for a conservative hash collision. This is an ordering contract for the listed local facts, not an authorization grant.

The implementation requires READ COMMITTED. An earlier REPEATABLE READ or SERIALIZABLE snapshot cannot be made current merely by acquiring this fence, so publication refuses those transactions. Shared-fence acquisition waits at most two seconds, restores the caller's lock_timeout and returns a registered availability error without aborting the surrounding transaction. The Harness records failure without publishing the answer. The Outbox records BLOCKED before any Gateway invocation; it may revalidate that same logical delivery later. It does not convert a known pre-send failure into UNKNOWN or replay an uncertain send.

The native single and group message transports now apply a 30-second total deadline across token retrieval and the one message request, in addition to existing HTTP timeouts and body budgets. A deadline before the message attempt remains NOT_ATTEMPTED; one after the message attempt remains UNKNOWN. Cancellation and unknown outcomes retain the existing non-retry contract. This bounds the native network portion of a transaction that holds the source fence; it is not a blanket deadline for all database or credential-provider work.

PostgreSQL documents that transaction advisory locks are released with the transaction and that READ COMMITTED takes a new snapshot per statement: [explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html), [transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html).

## Migration and compatibility

Migration f2a4b6c8d0e1 follows e1f3a6b8c5d7. It creates three functions and the fixed trigger set; downgrade removes only those objects. No business data is rewritten and existing constraints remain. Code and migration must be installed together; a PostgreSQL database without the migration does not silently fall back. SQLite remains a development/test adapter without this concurrency guarantee. No new model, connector credential or external write capability is introduced.

The registered authorization_fence_unavailable error and Outbox mapping are included in the exact producer manifest and catalog counts. Existing response/event schemas are retained. The ordinary running API/Web and real source-validation databases have not been migrated or deployed by this increment.

## Validation and remaining acceptance

Disposable PostgreSQL tests observe pg_blocking_pids, rather than assuming a lock wait from elapsed time. They cover publication-first commit/rollback, revocation-first current reads, concurrent publishers, cross-organization writes, bounded contention with a usable caller transaction, unsafe snapshot isolation and exact connector JSON serialization. Actual Harness and Outbox tests use real transactions and Policy/source checks, with synthetic model answers and a non-sending Gateway result. They verify that the answer/Outbox commits before a waiting revocation, that historical access closes afterward, and that contention can recover on the same logical delivery without duplicate send calls. The migration also passes downgrade/upgrade and schema-drift checks. Exact results are recorded in the phase report and metadata-only ledger.

Remote DingTalk grants still use the existing verified lease; this does not create a remote revocation event service. Lease expiry during transport preparation, final vendor-POST freshness, the compatibility IM path that hands plaintext to another process, broader authorization/lineage mutation surfaces and normal-runtime acceptance still require explicit treatment. Existing externally delivered messages cannot be recalled by a local database transaction. No complete M1, production promotion or complete enterprise-document coverage is claimed.
