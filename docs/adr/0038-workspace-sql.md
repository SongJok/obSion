# ADR 0038: Workspace SQL is a ledger of published SQL artifacts

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Workbench lists SQL beside Dashboard, Reports, and the Data catalog.
Data Runs already emit `ArtifactKind.SQL` from a validated governed query.
A SQL editor that invents SELECT text or executes the warehouse from the
workspace rail would be a demo.

## Decision

`GET /workspaces/{id}/sql` lists current `ArtifactKind.SQL` rows. The Workbench
SQL rail is read-only and renders published SQL text. It does not compile
metrics, open a DSN, or invent warehouse rows.

Conversation greetings and knowledge answers do not create SQL artifacts.
The Data catalog remains the metric/semantic surface; this rail is the
workspace SQL ledger.

This is not a query console, not SYSTEM text, and not vendor IM HTTP.

## Consequences

Operators can open validated SQL from Data Runs or authorized uploads. A later
evidence rail may list Evidence rows; it must not fabricate warehouse results
here.
