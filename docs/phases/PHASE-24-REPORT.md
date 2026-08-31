# PHASE-24-REPORT — Professional agents, skills, and workflow hardening

## What was implemented

Phase 24 closes the remaining specialist gap: Agents that existed in the registry
were not internally routed, and several named Skills were missing.

- Declarative Skills now cover `sql-analysis`, `business-analysis`, `trend-analysis`,
  `funnel-analysis`, `code-review`, `log-analysis`, `root-cause-analysis`,
  `report-generation`, and `support-diagnosis`. Each Skill has instructions,
  capability bounds, required/optional Evidence, and verification rules. YAML remains
  the source of truth over `builtins.py`.
- Understanding routes `SUPPORT`, `OPERATION`, and `ANALYTICS` without a user-facing
  agent picker. Knowledge, Data, Engineering, and Incident defaults are unchanged.
- `AgentRouter` pins `support-agent`/`support-diagnosis`, `operation-agent`/`log-analysis`,
  and `analytics-agent` (business/trend/funnel by question). Code review and SQL
  analysis are Skill overlays on Engineering/Data, not new user choices.
- Support planning searches authorized tickets then knowledge. Tickets are
  ACL-filtered DOCUMENT rows with `source=ticket` on the INTERNAL knowledge index.
  `ticket.search` is INTERNAL, bound to `obsion-knowledge-index`, and cannot create
  or close tickets.
- Operation planning is read-only status (`k8s.status`, deployment, config, logs,
  metrics). Unknown routes no longer fall through to the incident chain.

## Architecture decisions

Tickets reuse the knowledge ACL/chunk store instead of a second document fabric.
`knowledge.search` excludes `source=ticket` so KnowledgeAgent cannot silently cite
support tickets. Analytics stays on the governed compile path when metrics resolve,
so funnel/trend questions still execute `data.query` rather than an ungoverned
metric HTTP shortcut. Workflow resume, checkpoints, and read-only schedules remain
the earlier automation contract; this phase did not replace that runtime.

## Validation

- `uv run pytest` — 401 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase24_professional_agents.py` and the support-diagnosis e2e.
- Registry validation: 8 agents, 14 skills, 5 connectors.
- Evaluation validation: 32 cases across 3 datasets (7 ROUTING, 23 RUN_OUTPUT,
  2 SQL_POLICY).
- Event v1 `intent.detected` / `plan.created` route enums now include ANALYTICS,
  SUPPORT, and OPERATION. Checksums were updated; event count remains 93.

## Remaining risks

- Operation and Analytics e2e still depend on bound production connectors for
  `k8s.status` / metric HTTP; local tests assert planning and routing, not live
  cluster or warehouse calls.
- `ticket.search` is an index over ingested ticket documents, not a live Jira/ITSM
  adapter. A production connector can bind the same capability without changing
  the Skill or Harness contract.
