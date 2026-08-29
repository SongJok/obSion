# Phase 19 IncidentAgent and evidence-fusion review

## Review question

Can IncidentAgent execute a bounded, read-only investigation from metric baseline to
anomaly, dimensions, deployment, logs, and code diff; produce deterministic Top1/Top3
candidate root causes; and bind every root-cause Claim to at least two distinct Evidence
types without a model, repair path, restart, configuration write, or deployment write?

**Status: PENDING — automated checks do not constitute platform, security, or incident
owner approval.**

## Delivery contract

- The IncidentAgent and `incident-investigation` Skill are selected for explicit operational
  incident language. The planner creates the ordered dependency chain
  `metric.query → metric.compare → metric.anomaly → metric.dimension → deployment.list →
  log.aggregate → log.search → trace.search/config.diff/git.diff`, filtering unavailable
  capabilities through the pinned AgentSpec.
- Every external result still crosses the Capability Gateway and EvidenceFabric. The new
  `IncidentEvidenceFusion` consumes only current-Run Evidence and correlates timestamps,
  service, environment, deployment, commit, and bounded signal markers.
- Fusion emits no more than three ranked candidates. Each candidate carries rank, score,
  reason codes, Evidence IDs, and Evidence types. A candidate Claim must reference at
  least two different Evidence types; empty provider result sets cannot become signals.
- The answer Artifact stores an `incident_fusion` projection containing Top1/Top3,
  coverage, timeline, and unresolved conflicts. Critic verification receives those
  conflicts and marks unsupported or contradictory output PARTIAL; it never authorizes a
  production mutation.
- Golden RUN_OUTPUT cases can assert candidate count, Top1 Evidence types, and the number
  of cross-type Claims. Existing evidence, audit, replay, policy, and connector boundaries
  remain mandatory.

## Automated acceptance map

- `test_phase19_incident.py` covers metric/deployment ranking, Top1/Top3 bounds, ordered
  planning with repository lineage, explicit incident routing, conflict retention, empty
  result handling, and the two-type Claim gate.
- Full Python regression: 368 passed, 18 opt-in PostgreSQL tests skipped. Ruff passes and
  mypy passes for 138 source files. Error/event contracts remain 262/92; static registry
  remains 8 agents, 4 skills, 4 connectors. Evaluation validation remains 28 cases across
  3 datasets (23 RUN_OUTPUT, 3 ROUTING, 2 SQL_POLICY).
- Web lint, typecheck, tests (15), and production build pass. Compose configuration passes.
  CI's pinned `alpine/helm:3.18.4` container passes chart lint and template rendering.
- A fresh PostgreSQL 17 + pgvector 0.8.6 instance upgrades through the complete Alembic
  chain with no drift; integration regression reports 15 passed and 3 explicitly skipped
  destructive migration tests. The temporary database was removed after the run.

## Human review checklist

- Confirm metric baseline/anomaly semantics, dimension ACLs, deployment-to-commit lineage,
  log/trace query quotas, candidate wording, Evidence retention/classification, provider
  credentials, and production egress.
- Confirm the Golden incident fixtures represent real top-1/top-3 operator judgments and
  that every accepted root-cause Claim has two genuinely independent Evidence types.
- Confirm no repair, restart, configuration mutation, deployment mutation, or auto-PR
  capability is reachable from the IncidentAgent or Skill.
