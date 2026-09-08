# Phase 100 architecture review: context-first clarification

## Review question

Can the Harness resolve a vague request from already-authorized context and, only when
a blocking ambiguity remains, pause and resume the same durable Run without guessing,
leaking canonical values, bypassing Policy, or creating a second runtime protocol?

**Status: PROVISIONAL PASS for the implemented architecture and frozen contracts.
Final phase status remains pending the root task's complete validation run and recorded
test totals.**

## Delivery contract

- Intent resolution runs between Context and Plan in the existing
  Observe → Understand → Plan → Execute → Verify → Reflect → Respond loop.
- `Run.intent` is a strict, versioned, revisioned persistence contract. Typed
  `WAITING_USER` state must contain exactly one final active clarification; malformed or
  legacy waiting state fails closed.
- Candidate exploration is limited to principal-visible input, context references,
  conversation, governed memory, Workspace context, and organization-scoped catalogs.
  Repository discovery remains ACL-filtered. A single highest-confidence candidate is
  resolved; a missing value or unresolved tie is surfaced rather than guessed.
- Clarification is bounded to two rounds, three fields per round, and three options per
  field. Duplicate slots/options, repeated gaps, unsupported types, unbounded values,
  credential/private-endpoint slots, and server-derived slots are rejected.
- Public Run projection exposes `pending_clarification` with opaque option IDs and labels
  only. Internal canonical values, fingerprints other than the answer receipt,
  provenance, stored answers, remaining execution seconds, and answering principal are
  not projected.
- An answer is an organization-scoped write to the existing Run. It requires the active
  clarification UUID and expected Intent revision, exact field coverage, a live
  clarification deadline, and row-serialized state. Successful answers resume the same
  Run at planning with a fresh execution deadline based on the preserved remainder.
- Unanswered requests expire through worker reconciliation. Expiry closes the typed
  request, transitions `WAITING_USER → FAILED`, clears wait/lease/deadline state, and
  persists the registered `clarification_expired` error.
- REST, App Server, Web, and SDK entry points share this service contract. They do not
  implement candidate resolution, authorization, state transitions, or an Agent loop.

## Frozen Event boundary

The registry contains three additive v1 event contracts:

- `clarification.requested`: `clarification_id`, `intent_revision`, `round`, `question`,
  bounded public `gaps`, `requested_at`, and `expires_at`;
- `clarification.answered`: `clarification_id`, the new `intent_revision`, unique
  `answered_slots`, and `response_fingerprint`; and
- `clarification.expired`: a non-null `clarification_id`.

Every object is recursively closed. In particular, the requested-event schema rejects
`canonical_value`, `value_fingerprint`, `gap_fingerprint`, `request_fingerprint`,
`answers`, `remaining_execution_seconds`, `answered_by`, and `source_ref`. Event schema
checksums are pinned in the v1 registry.

## Error boundary

The machine-readable catalog adds:

- `clarification_answer_invalid` — HTTP 422 validation failure;
- `clarification_expired` — HTTP 409 active-request conflict and persisted expiry code;
- `clarification_not_pending` — HTTP 409 inactive/mismatched request;
- `clarification_stale_revision` — HTTP 409 optimistic-revision conflict; and
- `clarification_state_invalid` — HTTP 409 corrupt/non-resumable state.

Static producer analysis must prove exact origin coverage and reject an unregistered
literal entering any typed error sink.

## Persistence and rollback

Migration `e8b1c4d7f2a0_add_run_clarification_deadline` adds nullable, indexed
`runs.waiting_user_expires_at`. It does not rewrite historical Runs. Downgrade removes
the index and column; application rollback must precede database downgrade so no active
worker expects the deadline field.

## Automated acceptance map

- Event-contract tests validate all three payloads, registry hashes, recursive closure,
  required public fields, bounds, and explicit rejection of internal canonical state.
- Contract quality gates pin the Event/Error registries and their reviewed production
  producer manifests.
- Typed Intent tests cover strict parsing, lifecycle invariants, secret/derived-slot
  denial, fingerprints, exact answer coverage, stale revision, bounds, and fail-closed
  legacy/corrupt waiting state.
- Context exploration tests cover ACL-filtered explicit references, visible prior
  context, catalog ambiguity, no-candidate questions, and refusal to select the first
  metric/repository candidate.
- Service/runtime/worker tests cover row-serialized answer/resume, deadline freezing,
  expiry, Run/Event/Audit transitions, and continued planning on the same Run.
- REST/App Server/client/Web tests cover transport parity, opaque option selection,
  accessible interaction, idempotent request identity, and reconciliation.
- PostgreSQL migration tests cover upgrade, downgrade, re-upgrade, index/column shape,
  and drift in an isolated database.

## Final validation evidence

状态：PROVISIONAL，未满足独立阶段关闭条件。

2026-09-05 的本地 Python、JavaScript、PostgreSQL 不变量、历史迁移及契约检查见 [M0 验证](../phases/productization-m0-validation.md) 和 [Phase 100 实现记录](../phases/PHASE-100-REPORT.md)。本轮契约检查为 96 个事件、325 个错误码。全工作树通过不代替本阶段专属澄清迁移往返、完整构建及干净候选证据；这些未记录项继续阻塞阶段关闭。

## Boundaries retained

The phase does not grant production writes, expose connector credentials, invoke a
Capability without Policy, change Alpha.1 promotion eligibility, fabricate live tenant
evidence, or satisfy any operator-owned staging/UAT/security/publication gate. Semantic
LLM-based clarification, arbitrary dynamic forms, and unlimited recursive questioning
remain outside this contract.

