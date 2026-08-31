# Phase 58 Workspace dashboards review

## Review question

Do Data Runs that already produced a CHART publish a workspace DASHBOARD that
only references those artifacts, without inventing series or turning greetings
into dashboards?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Conversation greetings remain a single TEXT artifact and publish no DASHBOARD.
- Knowledge reports do not invent a DASHBOARD.
- `_workspace_dashboard_artifact` emits DASHBOARD only when a CHART exists.
- Dashboard content is panel references, not Vega encodings or fabricated values.
- `GET /workspaces/{id}/dashboards` lists current DASHBOARD artifacts.
- Workbench discloses that the rail does not fabricate data series.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_phase58_workspace_dashboards.py` covers chart composition, chart
  requirement, greeting/knowledge exclusion, tenant isolation, and UI/AST bans.

## Human review checklist

- Confirm operators do not treat dashboards as a signed BI SLA or Prompt text.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
