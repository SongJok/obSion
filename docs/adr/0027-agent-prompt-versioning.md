# ADR 0027: Agent and Prompt versions are compared and rolled back as immutable snapshots

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires Agent v1/v2 with A/B, Rollback, Compare, and Evaluate, and forbids
editing production Prompts in place. Studio already published checksummed Agent and
Skill versions and promoted an `active_version`. Prompt rows were immutable snapshots
without a runtime pin. A second Harness, percentage traffic split, or rewriting a
published spec would violate the registry and evaluation contracts.

Eval already compares two completed Evaluation Runs on a frozen Golden Dataset
snapshot. Runtime A/B would be a second router.

## Decision

Studio `POST /api/v1/studio/compare` diffs two published Agent, Skill, or Prompt
versions. Secret-bearing JSON paths are redacted. `traffic_split` is always false.
The response tells operators to pin each version on separate Evaluation Runs of the
same dataset snapshot. `fixtures.actual` remains rejected.

Studio `POST /api/v1/studio/rollback` restores a previously published Agent or Skill
version by promoting it. Both versions remain in the catalog. Prompt rollback is
denied: Prompt versions are snapshots, and V1 has no Prompt `active_version` cutover.
Operators publish a replacement Prompt version instead of editing production text.

This is not a canary router and not a Prompt pin on Harness Runs.

## Consequences

Engineers can compare Agent v1/v2, roll back the runtime Agent/Skill cutover, and
compare Prompt snapshots without mutating history. Runtime Prompt pinning on Turn/Run
and Eval remains a later gate. Vendor IM HTTP and remote connectors remain
unimplemented.
