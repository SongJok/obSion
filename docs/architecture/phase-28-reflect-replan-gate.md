# Phase 28 Reflect critic replan review

## Review question

When Critic reports missing required Evidence after VERIFY, can Reflect send the Run
back through authorized read-only capabilities without publishing, without unbounded
recursion, and without treating empty Evidence payloads as coverage?

**Status: PENDING — automated checks do not constitute production or security
approval.**

## Delivery contract

- Substantive Evidence filtering is shared by Critic and the gap scanner.
- `_apply_gap_replan` is the only insertion path. Reasons
  `critic_missing_evidence` and `critic_verification_failed` share
  `run_max_critic_replans`.
- Reflect decisions are `RESPOND`, `REPLAN`, and `WITHHOLD`. REPLAN does not start
  RESPOND. VERIFY is reopened so the next loop re-runs Critic.
- Unused Agent-authorized read-only capabilities only. No write path, no new Event
  type, no second Harness.

## Automated acceptance map

- `test_phase28_reflect_replan.py` covers empty `events` lists, Reflect REPLAN, and
  insertion of an unused metric capability.
- `test_phase27_harness_reflect.py` distinguishes REPLAN (missing types) from
  WITHHOLD (coverage/conflict without a type gap).
- Existing Phase 23 bounded replan tests remain required.

## Human review checklist

- Confirm tenant registries cannot bind write capabilities into critic replan
  selection.
- Confirm operators still treat `plan.updated` as the replan audit, not a new Event
  name.
