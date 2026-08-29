# Phase 16 DataAgent vertical-slice review

## Review question

Does a metric-bearing question travel through the governed DataAgent path from
understanding to semantic SQL execution and return auditable SQL/table/chart artifacts,
while a “why did it decline?” follow-up remains limited to approved data dimensions?

**Status: PENDING — automated checks do not constitute business or data-product approval.**

## Delivery contract

- Metric-bearing questions are internally pinned to the versioned `data-agent` and
  `governed-analytics` Skill. The skill exposes only governed data capabilities and
  explicitly forbids logs/traces for dimension-level decline analysis.
- Understanding → planning keeps metric questions on the DATA route even when the wording
  contains why/anomaly terms. The persisted plan includes the agent/skill snapshot and
  only the DataAgent capability set.
- DATA Evidence drives deterministic SQL, metric definition lineage, and the SQL/TABLE/
  CHART artifact set. Date/time dimensions produce a line trend chart with Vega-Lite
  `usermeta` linking Metric, Evidence, and SQL fingerprint.

## Automated acceptance map

- `test_phase16_data_agent.py` verifies specialist routing, Skill snapshot, DATA-only
  capability selection, and the absence of log/trace tools for decline questions.
- `test_data_artifacts.py` verifies SQL/table/chart materialization, metric metadata, and
  temporal trend encoding. Prior Knowledge, semantic, SQL policy, Evidence, Audit,
  OpenAPI, SDK, PostgreSQL, Compose, Helm, and static contract gates remain required.

## Executed gate evidence

The final automated gate run reports 356 Python tests passed with 18 opt-in PostgreSQL
tests skipped; Ruff and mypy pass. Contract validation reports 255 error codes and 92
event versions. The static registry reports 8 agents, 4 skills, and 4 connectors.
Evaluation validation reports 28 cases across 3 datasets (23 RUN_OUTPUT, 3 ROUTING,
and 2 SQL_POLICY). Root package lint, typecheck, tests, and build pass; Compose
configuration and pinned Helm lint/template checks pass. A disposable PostgreSQL 17
with pgvector 0.8.6 gate runs the migration/check/integration suite with 15 passed and
3 opt-in skips. Human review remains pending regardless of automation.

## Human review checklist

- Confirm the DataAgent metric and dimension golden set, including decline follow-ups.
- Confirm metric definitions, timezone/grain behavior, empty-result language, and chart
  semantics with data-product owners.
- Confirm production connector bindings, read-replica policy, execution-success threshold,
  and DATA Evidence retention/classification.
