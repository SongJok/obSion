# ADR 0039: Workspace Evidence is a join over persisted Evidence rows

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt Workbench lists Evidence beside Files, SQL, and Runtime. Evidence is
already durable on each Run (`GET /runs/{id}/evidence`). The workspace had no
ledger of those rows. Inventing citations or warehouse cells at the rail would
be a demo.

## Decision

`GET /workspaces/{id}/evidence` lists current `Evidence` rows whose Run belongs
to a Thread in that workspace. The query joins Evidence → Run → Turn → Thread
and stays tenant-scoped. Conversation greetings persist no Evidence.

The Workbench Evidence rail is read-only and renders the stored `content`. It
does not call the model, retrieve new documents, or invent fingerprints.

This is not a second Evidence store, not SYSTEM text, and not vendor IM HTTP.

## Consequences

Operators can open cited DOCUMENT (and later DATA/CODE) evidence without
selecting a Run first. Replay still copies Evidence onto the replay Run. A
later runtime timeline may list Events the same way; it must not fabricate
steps here.
