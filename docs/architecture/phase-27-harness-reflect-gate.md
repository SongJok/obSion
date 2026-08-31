# Phase 27 Harness REFLECT step review

## Review question

Does every ordinary Run persist Reflect as a first-class step between Verify and
Respond, with a recorded publication decision, without a new Event type, without a
second Harness, and without changing production read-only or Policy boundaries?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `StepKind.REFLECT` is stored on `run_steps.kind`. Alembic widens the PostgreSQL
  CHECK to include `OBSERVE` and `REFLECT`.
- Greeting Runs persist
  `OBSERVE → UNDERSTAND → PLAN → VERIFY → REFLECT → RESPOND`.
- Capability-failure Runs skip VERIFY, REFLECT, and RESPOND through the existing DAG
  cascade.
- Missing-evidence replan moves VERIFY, REFLECT, and RESPOND ordinals together and
  restores `depends_on`.
- REFLECT `output_ref` is `reflect.respond` or `reflect.withhold`. Unverified factual
  Runs still follow Phase 20 WITHHOLD/PARTIAL publication; this phase does not add a
  second critic-failure replan loop.
- No new Event catalog entry. No Java backend. No production write path.

## Automated acceptance map

- `test_phase27_harness_reflect.py` covers decision helpers and greeting persistence.
- `test_phase7_harness_core.py` covers the six-step greeting graph and seven-step
  production-failure skip cascade.
- `test_phase23_evidence_critic_memory.py` covers ordinal movement of REFLECT.
- `test_step_executor.py` covers VERIFY → REFLECT skip propagation.
- `test_phase26_experience_cli.py` requires REFLECT in CLI-inspected kinds.
- OpenAPI `StepKind` enum includes `REFLECT`.

## Human review checklist

- Confirm replay of Phase 26 Runs that lack REFLECT rows still completes.
- Confirm operators do not treat `reflect.withhold` as a new Event contract.
