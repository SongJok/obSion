# ADR 0121 — Recheck source access at the final DingTalk send boundary

- Status: accepted for development; ordinary-runtime and live document-answer acceptance remain pending.
- Date: 2026-09-12
- Builds on: ADR 0119–0120 and the durable DingTalk Outbox.

## Decision

Token acquisition can outlive a source-access lease, an audience verification or an Outbox claim. The internal Gateway now requires an authorization callback for each durable robot send. The native single and group transports invoke it after obtaining the application token and immediately before attempting their single message POST. Only the boolean `True` permits that attempt. The callback is internal code, never an Agent argument, credential or serialized request field.

The Outbox callback checks its current claim expiry and reloads the same dispatch inputs used by its initial validation: trusted installation, organization, recipient binding, connector fingerprint, answer fingerprint, classification, group audience, Policy and managed-source access. It runs in the same transaction and source-publication fence as the dispatch. Source expiry and audience expiry therefore get another time-sensitive check after token retrieval; authorization writes retain ADR 0120's transaction ordering. The 30-second native send deadline includes the callback. A refusal or timeout before POST is known not to have sent; the existing bounded queue/revalidation path applies. An uncertain attempted POST remains UNKNOWN and is not blindly resent.

Group membership in a workspace does not establish permission to every source cited by an answer. Before selecting FINAL group delivery, the service now checks the managed-source lineage for each verified audience member under that member's current principal and Policy, with the actual installation corporation. If any member cannot read it, the existing fixed STATUS response is selected when permitted. The final callback repeats this selection; a change from the queued body or mode prevents that send. No enterprise body, title or citation is placed in the fixed status message.

The old IM prepare interface returns plaintext to another process, so its authorization check cannot cover a later network send. It may continue returning ordinary answers and fixed group status, but it must refuse managed-source bodies, including inherited managed lineage, with the existing `im_delivery_denied` error. This ADR explicitly approves that compatibility narrowing: managed enterprise content must use the governed durable send boundary. It does not silently migrate or resend old deliveries.

A rejected legacy prepare rolls back its business transaction. The HTTP boundary then records a content-free rejection in a separate transaction, linked to a fresh Policy evaluation. This does not preserve or claim to preserve the original rolled-back Policy row, and it does not commit failed delivery mutations. The audit contains the run identifier, stage and registered reason code, without answer text or connector secrets.

## Compatibility and migration

No public request or response schema changes and no new error codes are needed. The native transport callback is optional for existing direct internal callers; the production durable Gateway entrypoint requires it. Existing ordinary-answer, fixed-status and receipt-reconciliation behavior is retained. The managed-body restriction also applies wherever the legacy service reuses its authorized-payload routine.

Reuse migration `f2a4b6c8d0e1`; no new database migration or data rewrite. Production PostgreSQL requires ADR 0120's fence migration. Tests use fresh databases migrated from empty to the current head. No ordinary-runtime database or service is changed by this increment.

## Validation and limits

The focused tests use an actual Harness, Gateway, Policy Engine, credential broker and durable Outbox with explicitly synthetic model output and HTTP responses. They verify successful single/group sends, source/claim expiry during token retrieval, audience expiry, and a second workspace member who lacks access to the managed source. A failure-first regression showed that this second member previously received the cited body. Native transport tests verify callback ordering, strict approval and timeouts for both send types. Legacy HTTP validation proves that otherwise readable managed content is withheld and its denial audit persists without a delivery row. The same end-to-end cases run against disposable PostgreSQL databases.

Exact test counts and source hashes belong in the increment's evidence ledger. These checks do not count as live DingTalk delivery, real model quality acceptance or a full Python suite rerun. Remote grants still depend on the existing verified lease; checking before POST cannot recall a message after a remote server accepts it. Broader provenance mutation surfaces, unsupported document formats, normal-runtime integration and live Pointbot document Q&A remain open. M1 and production promotion are not declared complete.
