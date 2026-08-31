# PHASE-07-REPORT — Harness core loop and AgentSpec

> Retrospective Phase 80 record based on the implemented Harness and current gates;
> it does not replace the pending architecture review.

## Delivered

- Persisted Observe → Understand → Plan → Execute → Verify → Reflect → Respond as
  Run Steps, with deterministic Capability DAG scheduling.
- Made GeneralAgent the sole user-facing entry and bound behavior to immutable AgentSpec
  profile, risk, budget, Skill, Capability, memory, and sandbox declarations.
- Kept factual answers Evidence/Claim-gated and greetings explicitly non-factual.

## Migration and validation

No second runtime or Event schema was created. Phase 80 reran Harness core, executor,
registry, Critic, production-access failure, and complete regression gates.

## Remaining boundary

AgentSpec can request only registered governed capabilities; it never embeds a model
vendor, connector, endpoint, or credential.
