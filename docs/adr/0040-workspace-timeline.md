# ADR 0040: Workspace Timeline is a join over persisted Run Events

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Workbench lists Runtime Timeline beside Evidence and Tool Calls.
Events are already durable on each Run (`GET /runs/{id}/events`). The workspace
had no ledger of those rows. Inventing Observe→Respond steps or reading Kafka
would be a demo.

## Decision

`GET /workspaces/{id}/timeline` lists current `Event` rows whose `run_id`
belongs to a Run in that workspace. The query joins Event → Run → Turn →
Thread and stays tenant-scoped. It does not invent event names or stream from
a second log.

The Workbench Timeline rail is read-only and renders stored payloads. The Run
inspector remains the per-Run cursor.

This is not Kafka, not ClickHouse, not SYSTEM text, and not vendor IM HTTP.

## Consequences

Operators can scan Harness events without selecting a Run first. Replay still
copies Events onto the replay Run. Marketplace and vendor IM HTTP remain
out of scope.
