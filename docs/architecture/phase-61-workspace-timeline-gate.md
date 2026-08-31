# Phase 61 Workspace timeline review

## Review question

Does the workspace expose persisted Run Events without inventing Harness
steps or introducing Kafka/ClickHouse as a V1 log?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- An empty workspace lists no timeline events.
- A greeting Run's events appear on `GET /workspaces/{id}/timeline` with the
  same ids as `GET /runs/{id}/events`.
- Other workspaces and tenants do not see those rows.
- Workbench discloses that the rail does not fabricate a timeline.
- Vendor IM HTTP remains unimplemented.

## Automated acceptance map

- `test_phase61_workspace_timeline.py` covers publication, isolation, tenant
  isolation, and UI/AST bans.

## Human review checklist

- Confirm operators do not treat this rail as a signed SLA or Prompt text.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
