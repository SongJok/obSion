# Phase 100 report: context-first clarification

状态：PROVISIONAL。实现记录已存在，但未纳入正式 completed_phases，不是阶段完成或生产晋级声明。2026-09-05 的工作树复验见 [M0 验证](productization-m0-validation.md)。

## What was implemented

Phase 100 makes "explore first, ask only when necessary" a durable Harness behavior
rather than prompt advice.

- **Typed Intent lifecycle**: `Run.intent` now carries a strict v1 `RunIntent` with
  monotonic revision, preparation stage, resolved-slot provenance, and a validated
  clarification history. Legacy non-waiting Runs stay readable; malformed
  `WAITING_USER` state is rejected rather than guessed.
- **Context-first exploration**: the resolver examines principal-visible explicit
  context, conversation, governed memory, Workspace context, repositories, and metric
  catalogs before asking. Deterministic confidence ranking resolves one winner and
  surfaces zero/tied candidates. It never silently takes the first catalog row.
- **Bounded questions**: at most two rounds, three fields, and three options per field;
  strict single-value `STRING`/`TIME_RANGE` inputs; opaque option IDs; repeated-gap
  detection; normalized, bounded free text; and explicit denial of credential,
  private-endpoint, or server-derived slots.
- **Safe public projection**: Run views expose a typed `pending_clarification` containing
  only the question, bounded gaps, opaque IDs, labels, revision, round, and timestamps.
  Canonical values, resolution provenance, internal fingerprints, stored answers,
  answering identity, and preserved execution seconds remain server-side.
- **Atomic answer/resume**: answers require Workspace write access, lock the Run,
  validate active ID, deadline and expected revision, require exact field coverage,
  map option IDs server-side, increment the revision, and resume the same Run at Plan.
  Stable error codes distinguish invalid, inactive, expired, stale, and corrupt state.
- **Paused execution budget**: `WAITING_USER` has its own indexed expiry. The Harness
  preserves the positive execution-time remainder while waiting, releases the lease,
  and restores a fresh execution deadline after a valid answer. Worker reconciliation
  expires unanswered requests and fails the Run deterministically.
- **One protocol across clients**: REST and App Server commands, Workbench interaction,
  and maintained SDKs project or answer the same control-plane object. None owns a
  second state machine or Agent loop.

## Architecture decisions

ADR 0079 records the context-first ordering, typed/revisioned persistence, bounded form,
credential/derived-slot denial, server-only canonical mapping, atomic same-Run resume,
separate wait/execution deadlines, and one Event/Audit plane.

The implementation preserves the core model:

`Workspace → Thread → Turn → Run(WAITING_USER) → Run(RUNNING) → Step → Event`

Clarification does not create another Turn or Run. It pauses the current Run between
Understand and Plan, then continues that Run from the authoritative Intent revision.

## Event and error contracts

- Added strict v1 schemas for `clarification.requested`, `clarification.answered`, and
  `clarification.expired`, with SHA-256 pins in the frozen registry.
- The requested payload deliberately mirrors the positive public projection and rejects
  canonical values, internal fingerprints, source references, stored answers, and
  execution-budget state at every nested object boundary.
- Added `clarification_answer_invalid`, `clarification_expired`,
  `clarification_not_pending`, `clarification_stale_revision`, and
  `clarification_state_invalid` to the machine-readable Error catalog.
- Updated reviewed Event/Error producer manifests so static analysis remains an exact
  proof of registered production sinks, including Run terminal error persistence.

## Migration

`e8b1c4d7f2a0_add_run_clarification_deadline` adds nullable indexed
`runs.waiting_user_expires_at`. The migration is additive and performs no historical
data rewrite. Its downgrade removes the index and column.

## Validation

- 2026-09-05 M0 工作树最新复验：Python 本地 1202 项通过、23 项跳过、6 项 live 排除；JavaScript 全工作区 258 项通过，Java SDK 6 项通过。该统计覆盖整个工作树，不是 Phase 100 专属验收。
- 临时 PostgreSQL 完整升级与 Alembic 漂移检查通过；16 项不变量和 5 项历史迁移往返通过。澄清迁移的专属数据保留/往返证据尚未单独记录。
- Ruff、严格类型检查及 Event/Error、Registry、评测门、发布说明、候选契约检查通过。候选契约的 `promotion_eligible` 仍为 false。
- Java SDK 测试及 JavaScript 全工作区构建已通过；隔离配置生成的 OpenAPI 与登记契约完全一致。澄清迁移专项及干净候选快照验证仍未完成，不能据此关闭本阶段。详细证据与后续更新以 M0 验证记录为准。

The contract-focused validation already defines explicit regressions for canonical-value
leakage, unknown nested fields, invalid UUID/fingerprint/slot shapes, unanswerable gaps,
and unregistered Event/Error producers. Final aggregate results must be written only
after the root task completes the shared worktree validation.

## Remaining operator gates

Phase 100 does not advance production promotion. Clean staging deploy/UAT,
staging-scoped timed restore, HIGH/CRITICAL registry policy, signatures, live OIDC and
secret-manager/read-replica evidence, security/data-owner approval, and maintainer
publication authority remain PENDING. No local clarification test can satisfy or
fabricate those operator-owned gates.

