# ADR 0006: Harness REFLECT is a first-class RunStep

- Status: Accepted
- Date: 2026-08-29

## Context

The Harness contract is Observe → Understand → Plan → Execute → Verify → Reflect →
Respond. Through Phase 26 the durable graph stored VERIFY then RESPOND. Critic
results lived on Claims, verification assessments, and `critic.completed` events, so
Replay and Workbench could not show Reflect as its own step. goal.txt requires
verification failure to replan rather than to publish immediately; that decision must
be a persisted step, not a hidden branch inside `_respond`.

## Decision

Every ordinary Run inserts a `REFLECT` RunStep between `VERIFY` and `RESPOND`. The
step depends on VERIFY. RESPOND depends on REFLECT. After Critic completes, Harness
records the publication decision (`RESPOND` or `WITHHOLD`) on the REFLECT step
(`output_ref=reflect.respond|reflect.withhold`) before creating Claims or the answer
Artifact. A `REPLAN` decision does not complete REFLECT; it reopens VERIFY and
returns the Run to Execute. Missing-evidence replanning must move VERIFY, REFLECT,
and RESPOND ordinals together, restoring `depends_on` so the DAG stays VERIFY →
REFLECT → RESPOND.

Event catalog remains frozen. Reflect does not add a new event type; step lifecycle
uses `run.step.*` and the existing `critic.completed` payload. Phase 28 attaches
critic-gap replan to the REFLECT decision without changing this Event contract.

Capability-failure skip cascade treats REFLECT like VERIFY and RESPOND. Legacy
fixtures without a REFLECT row remain publishable: `_start_core_step` no-ops on
`None`.

## Consequences

Workbench, CLI, Replay, and evaluations see the same six-kind core loop. PostgreSQL
CHECK constraints on `run_steps.kind` include `OBSERVE` and `REFLECT`. Phase 28 lets
Reflect `REPLAN` when required Evidence types are still missing and authorized
read-only capabilities remain; conflict-only failures still withhold.
