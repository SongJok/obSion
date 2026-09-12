# ADR 0123 — Keep managed-source status polls independent of model locks

- Status: accepted for development.
- Date: 2026-09-12
- Builds on: ADR 0119 and real source-answer validation in ADR 0122.

A real managed-source answer completed in approximately 43 seconds, but its progress poll timed out after 25 seconds. An isolated database diagnostic confirmed the cause with `pg_blocking_pids`: the metadata projection's source Policy insert referenced `policy_decisions.run_id` and waited for the model transaction's Run row lock. The permission check itself should not require the model to finish.

The metadata projection continues to execute the entire current source-lineage and Policy check, and continues to persist its audit. Its Policy decision records the Run identifier in the resource, and the audit records the same correlation/resource identifier plus the decision identifier. This read-only projection omits the optional direct Policy-to-Run foreign-key assignment so the insert does not acquire a conflicting Run key-share lock. This changes audit linkage, not the authorization decision. Model execution and final publication retain their existing direct Run link by default; no schema or migration is required.

Two failure-first PostgreSQL tests hold the Run row locked and call the actual HTTP status route. With both valid and revoked source access, the request must finish before releasing the lock. Revoked results still redact the plan and intent; the committed audit must retain its Policy identifier, outcome and Run resource/correlation. A separate real validation checks the running development API on the previously completed real answer. Status means committed durable state; this change does not invent uncommitted progress or remove the source check.

The normal environment is not upgraded by this increment. Other content reads and the model's own execution transaction have separate locking contracts; this decision does not claim that every endpoint is nonblocking. Provider latency and the quality of insufficient-evidence responses remain distinct work.
