# ADR 0037: Workspace Dashboards compose published CHART artifacts

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Workbench lists Dashboard beside Files, Reports, SQL, and Runtime.
Data Runs already emit SQL, TABLE, and CHART from cited warehouse evidence.
Inventing Vega encodings or numeric series at the workspace rail would be a
demo dashboard.

## Decision

A completed Run publishes at most one additional `ArtifactKind.DASHBOARD` after
result artifacts are flushed, and only when a `CHART` already exists. The
dashboard `inline_content` stores panel references (`artifact_id`, `kind`,
`title`) plus `chart_artifact_ids` / `table_artifact_ids` / `sql_artifact_ids`.
It does not copy encodings or invent `data.values`.

Greetings, knowledge answers, and chart-less Runs do not publish a dashboard.
Existing DASHBOARD rows are not duplicated. `GET /workspaces/{id}/dashboards`
lists current DASHBOARD artifacts. The Workbench Dashboards rail is read-only
and renders the referenced CHART/TABLE/SQL previews.

This is not a BI fabric, not SYSTEM text, and not vendor IM HTTP.

## Consequences

Operators can open a data Run's real charts as a workspace dashboard. Replay
copies the published DASHBOARD. A later SQL rail may list SQL artifacts; it
must not invent warehouse rows here.
