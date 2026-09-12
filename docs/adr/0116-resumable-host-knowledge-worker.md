# ADR 0116 — Resumable host knowledge synchronization

- Status: accepted for the development productization slice; production activation remains gated.
- Date: 2026-09-12
- Builds on: ADR 0113, ADR 0114, ADR 0115.

## Problem

Successful one-off document reads do not provide an autonomous knowledge source. The source needs a durable discovery cursor, fixed organization and connector identity, independent document failure handling, and acceptance fenced against a stopped or superseded worker. A passive DWS profile listing also reports an expired access token without attempting its supported refresh; treating this as permanent permission denial prevents otherwise authorized reading.

## Decision

Keep one Python control plane. `knowledge.sync_worker.KnowledgeSyncWorker` claims one source from PostgreSQL and processes one workspace page, child page or document through the Capability Gateway. It saves every successful page before releasing its lease. Reconstructing the worker does not reconstruct its progress from memory. Opaque cursors are preserved; repeated cursors, repeated nodes and cycles are errors. Only fully exhausted enumeration followed by document attempts permits the missing-item sweep. A failed document does not delay unrelated documents with an increasing directory retry delay.

`OperatorGatewayRequest.connector_id` optionally pins an existing, enabled, same-organization binding. A pin never falls back to another connector. Its idempotency fingerprint includes the requested connector; omitted pins preserve the previous fingerprint exactly. All Policy, grants, schema, rate, broker and audit checks remain in place.

The explicit development SDK adapter adds `knowledge.dingtalk.discover`, a fixed read-only operation with two stages. Workspace pages contain at most 30 records; child pages contain at most 50. Discovery performs fresh native organization and parent checks. Node authorization retains the existing workspace authorization page budget: exceeding it is an incomplete/error state, never a successful truncated scan. Durable traversal has no former 200-node enumeration cutoff; directory depth is bounded at 64. Cursor size is bounded at 8,192 characters. Unsupported files and folders remain visible as item states, not fabricated documents.

A worker lease lasts 120 seconds and each unit has a 100-second processing deadline. Acceptance locks and rechecks the source token, generation and expiry. Connector and principal scope are rechecked before directory acceptance; document acceptance also retains ADR 0115 checks. Stale completion cannot publish or mark missing. If a failure checkpoint loses its lease after the initial lock, the entire transaction also rolls back both item changes and bulk access invalidation. Source disabling immediately invalidates the lease. Retry records contain registered error codes and safe audit metadata, without upstream text, credentials or cursors in logs.

The host reader may execute the documented fixed `dws --profile <corp:user> auth status` command only for a uniquely matched expired identity. It re-lists profiles and verifies the same corporation, user and active status afterward. It never selects the global default, switches organizations, performs an interactive login, or accepts a successful refresh response as identity proof by itself.

`python -m obsion.knowledge.sync_host` is an explicit development host entrypoint. It requires PostgreSQL and durable MinIO storage, installs only the managed SDK executor, and does not start a second API backend, run migrations or start unrelated FastAPI lifespan workers. `--once` performs at most one bounded unit. Registration, capability activation and policies must exist before running it.

## Compatibility and migration

Reuse migration `e1f3a6b8c5d7`; no additional table change is needed. Existing unpinned Gateway callers, legacy DingTalk connectors and default API executor installation are unchanged. The ordinary API container is not implicitly granted the host's DWS login.

Rollout order: apply ADR 0115 migration, install the current control-plane code, explicitly register managed read/discovery capabilities and same-organization bindings, register the source under its current operator and start the host worker. Stop/disable a source before retiring its host. Do not downgrade away the source-access tables while this code is serving knowledge retrieval.

## Validation and limitations

See `docs/phases/productization-dingtalk-org-documents-validation.md` and the metadata-only real-worker ledger. Tests exercise restart at each page, opaque continuation, retry after repeated/malformed pages, wrong-organization connector alternatives, revocation, superseded worker fencing and expired-login refresh.

This does not mark enterprise productization complete. Source management UI/API and normal development deployment remain to be completed. Complete JSONML coverage, AI tables, authorization at final answer publication/history, large-source performance, user-facing synchronization health, and Kimi/IM answers grounded in automatically synchronized documents still need separate acceptance. A finished scan with PARTIAL or DENIED items is not proof that every document was ingested.
