# ADR 0119 — Current source access for historical and derived answers

- Status: accepted as an incremental development control; concurrent revocation and normal-runtime acceptance remain open.
- Date: 2026-09-12
- Builds on: ADR 0115–0118, the durable Harness lineage and the existing delivery contracts.

## Decision

A published answer is not a permanent grant to its source. Resolve managed evidence through the same-organization Run graph before returning historical content or using it again. Dependencies include replay origins, platform artifact origins and conversation source Runs. New Runs record the conversation sources actually supplied to the answer author in their existing plan; older Runs conservatively retain all captured dependencies. A GENERAL answer that used no enterprise history therefore remains available when unrelated earlier sources are paused. Conversation inspection independently filters each snapshot, including snapshots not used by that answer.

Resolution is bounded at 1,000 Runs and 10,000 evidence records. Missing or cross-organization dependencies, invalid identifiers or an exceeded budget deny content; a truncated graph never counts as a successful empty graph. This is an intentionally conservative read-time traversal, not a claim of scalable flattened provenance. User-authored memories and independent uploads do not receive inferred lineage. Platform-generated artifact reuse and replay do retain their source dependencies.

Current identity, knowledge.read Policy, document ACL, managed-source lease and current version are checked through the shared guard. Apply it to REST and AppServer historical content, evidence, claims, reports, artifacts, event replay/SSE polling, workspace lists, conversation capture, attachment ingestion and replay admission/materialization. Lists omit inaccessible records; direct content access returns the existing not-found response. Task status and control remain usable: an additive source_content_available flag is false and sensitive intent, plan, compaction, clarification and error prose are removed from the public Run view. The Web conversation hides cached answer artifacts and copy/replay actions and explains the permission or version change.

Read handlers use a dedicated read-only transaction boundary so registered denials still persist Policy-linked, content-free audits. This boundary is not used to commit failed mutations. Existing write transactions retain their own rollback behavior.

The DingTalk Outbox checks source access both when queueing and immediately before dispatch, using the recipient identity and the trusted installation corporation. Managed documents must all belong to that exact corporation. A changed source or corporation blocks the queued item without invoking the Gateway. The compatibility IM path also checks the final payload; without a trusted corporation it cannot send managed content. Fixed, content-free group status messages preserve their existing behavior. Receipt reconciliation remains metadata-only.

## Idempotent AppServer responses

An immutable operation receipt is not a reusable source grant. After resolving the original request outcome, recheck current workspace and source access for turn.create, run.cancel, run.replay and run.clarification.answer responses. Do not execute the mutation again or rewrite its ledger. While access remains valid, return the original payload exactly; if access changed, redact its sensitive Run fields using the same public projection. A PostgreSQL failure-first test demonstrated the old cached-response bypass; the retained regression also asserts one immutable request record and its original result.

## PostgreSQL replay correction

Real PostgreSQL validation exposed an existing replay ordering defect that SQLite did not reject: scalar foreign-key assignments did not cause SQLAlchemy to insert cloned evidence before claim/evidence links. Flush parent groups explicitly (steps, evidence, claims, artifacts, verification assessments and claim verification results) before dependent groups. Keep every composite foreign-key constraint and the surrounding transaction. This changes persistence ordering, not the replay contract.

## Compatibility and migration

No schema migration or new external adapter. Reuse e1f3a6b8c5d7, existing lineage columns and Run plan JSON. source_content_available is an additive response field and is optional in the Web client for compatibility with older responses. Regenerate OpenAPI and retain registered error/event contracts. No credentials or document bodies are added to access audits.

## Validation and remaining boundaries

Fourteen synthetic Harness scenarios cover the three generation checkpoints, expiry/Policy/version/ACL changes, missing source labels, historical redaction and audit, replay descendants, source/corporation changes before queued delivery and unrelated everyday conversation. Web tests assert cached restricted content and actions disappear. Five scenarios also pass against separate, fully migrated disposable PostgreSQL databases: after_publish, replay_copy, queued_reply, wrong_reply_corp and unused_conversation. These PostgreSQL tests use synthetic models and a non-sending Gateway boundary, not live DingTalk or Kimi. See the linked validation report and metadata-only ledger for exact check results.

The final check and transaction commit or network dispatch are not atomic. PostgreSQL concurrent revocation tests and an explicit serialization/fencing contract remain necessary before normal-runtime activation. Already delivered external messages or downloaded content cannot be retrospectively withdrawn by this local guard. Large lineage performance, manual memory provenance policy, unsupported document formats/AI tables, persistent normal-runtime synchronization and a new real pointbot document-answer acceptance remain open. This ADR does not complete M1 or authorize production promotion.
