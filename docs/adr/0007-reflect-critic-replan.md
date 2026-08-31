# ADR 0007: Reflect may replan before publication

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires Verify → Reflect → replan on verification failure, otherwise
Respond. Phase 23 inserted missing-type capabilities before `_respond`. Phase 27
persisted REFLECT but still published WITHHOLD when Critic failed. Empty Evidence
rows (for example `events: []`) counted as covering a required type in the pre-publish
gap scan, while Critic ignored them as non-substantive. The two views of "missing"
could disagree, and Reflect could not send the Run back to Execute.

## Decision

Gap insertion is a shared Harness operation (`_apply_gap_replan`). It counts both
`critic_missing_evidence` and `critic_verification_failed` against
`run_max_critic_replans`. The pre-Respond scan uses the same substantive Evidence
filter as Critic. After VERIFY, Reflect decides `RESPOND`, `REPLAN`, or `WITHHOLD`.
`REPLAN` applies only when unused, Agent-authorized, read-only capabilities can fill
`critic.missing_evidence`. On success VERIFY is reopened, RESPOND is not started, and
Execute runs the new wave. If no capability can be selected, Reflect records
`WITHHOLD` and publication follows Phase 20 PARTIAL/WITHHOLD rules.

No new Event type is added. Replan still emits `run.state_changed` and `plan.updated`.

## Consequences

Empty connector payloads no longer hide required types. Conflict-only Critic failures
still withhold rather than guessing extra tools. A later Experience client (IDE/IM)
does not need its own replan loop; it watches the same Run.
