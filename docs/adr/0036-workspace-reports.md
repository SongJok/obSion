# ADR 0036: Workspace Reports are published Harness REPORT artifacts

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Workbench lists Reports beside Files, Tasks, and Runtime. Engineering
Runs already emit a CODE/GIT `REPORT`. Knowledge, Data, and Incident answers
stayed on `TEXT` only, so the workspace had no report ledger. Inventing a
dashboard of fake charts would be a demo.

## Decision

A completed Run publishes at most one additional `ArtifactKind.REPORT` when it
has citations, data/engineering result artifacts, or incident fusion. Conversation
greetings and unknown knowledge answers do not. Engineering Runs that already
emitted a REPORT are not duplicated.

The conversation answer remains `TEXT` at `artifacts[0]` for existing clients.
The REPORT copies redacted markdown, citations, and verification, and points at
the answer artifact. `GET /workspaces/{id}/reports` lists current REPORT rows.
The Workbench Reports rail is read-only.

This is not a dashboard fabric, not SYSTEM text, and not vendor IM HTTP.

## Consequences

Operators can open cited or evidenced answers as workspace reports. Replay copies
the published REPORT. A later dashboard must compose real CHART artifacts; it
must not invent series here.
