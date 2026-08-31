# PHASE-23-REPORT — Evidence, Critic, and Memory hardening

## What was implemented

Phase 23 closes the remaining Phase 12/13 contracts: Critic-triggered bounded
replanning, first-class GIT/SQL Evidence types, Engineering CODE/DIFF/REPORT
artifacts, and an inspect/edit/delete Memory lifecycle.

- After a capability wave, Critic computes missing required Evidence types. Harness
  may append unused, Agent-authorized, read-only capabilities (at most one per missing
  type) before VERIFY/RESPOND. `run_max_critic_replans` defaults to 1.
- Transient retries still run first. A critic-added wave can itself receive one
  transient recovery. A second critic wave is refused even if the gap remains.
- `git.commit` / `git.diff` / `git.blame` / `git.history` emit `GIT` Evidence. Code
  Graph capabilities remain `CODE`. `DATA` and `SQL` are aliases for required-type
  coverage so `data.query` does not break existing DATA plans.
- Engineering and incident Git/code results materialize CODE, DIFF, and REPORT
  artifacts with Evidence lineage. Critic coverage is recorded as the
  `obsion.critic.evidence_coverage` histogram.
- Memory supports GET inspect, PATCH edit (returns to CANDIDATE and re-runs policy),
  and DELETE revoke. Expired candidates/approvals become EXPIRED on read. Revoked
  items remain inspectable and emit `memory.revoked`.

## Architecture decisions

Missing evidence is a plan gap, not a model failure. The selector never retries a
capability that already ran, never introduces a write, and only uses capabilities
already on the Agent spec. GIT is a distinct cause artifact from CODE so incident
fusion can pair a metric/log signal with a commit without conflating static graph
hits. SQL is an alias of DATA rather than a second persist of `data.query`, preserving
the existing required-evidence contract.

## Validation

- `uv run pytest` — 395 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase23_evidence_critic_memory.py` and memory inspect/edit/revoke e2e.

## Remaining risks

- A missing type whose only mapped capabilities already returned empty sets still
  publishes PARTIAL/WITHHOLD. That is intentional; repeating the same read cannot
  create Evidence.
- PostgreSQL deployments need Alembic revision `c23e1d4a9b70` before persisting GIT,
  SQL, or REVOKED values.
