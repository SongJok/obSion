# Governed workspace collaboration

## Purpose

Workspace collaboration converts investigation results into durable follow-up work and
explicit team decisions. It is not a generic checklist: task progress and decision
formation are governed records that share the same tenant, workspace, event, audit,
redaction, and provenance boundaries as Runs and Evidence.

The design has two aggregates:

- `WorkspaceTask` is a mutable lifecycle record protected by optimistic concurrency
  and a database-enforced state machine.
- `WorkspaceDecision` is a mutable disposition header over immutable,
  content-addressed `WorkspaceDecisionVersion` rows.

Neither aggregate has a delete endpoint. Retention must use a future governed workflow
that leaves an auditable tombstone; direct database deletion is rejected.

## Task contract

A task stores title, description, priority, optional workspace-member assignee,
optional deadline, optional source Run, completion time, and an integer version.
Allowed status transitions are:

```text
OPEN ───────────▶ IN_PROGRESS ───────────▶ COMPLETED
  │                    │                       │
  ├──────────────▶ BLOCKED ◀──────────────────┤
  │                    │                       │
  └──────────────▶ CANCELLED                  └──▶ OPEN
                         └────────────────────────▶ OPEN
```

`BLOCKED` may move to `OPEN`, `IN_PROGRESS`, `COMPLETED`, or `CANCELLED`.
Every successful mutation increments `version` by exactly one. Clients submit
`expected_version`; stale updates return `409 workspace_task_version_conflict` instead
of overwriting another member's work. `completed_at` is present if and only if the
task is completed.

Task identity, organization, workspace, creator, source Run, and creation time are
immutable. The assignee must be an active owner or member of the same workspace. A
source Run must belong to that workspace.

## Decision contract

The decision header stores workspace, lifecycle, current version, creator,
disposition actor/time, optional source Run, and optional decision being superseded.
Content lives only in immutable version rows:

- title and summary;
- rationale and alternatives considered;
- creator and creation time;
- canonical SHA-256 content fingerprint.

A decision begins as `PROPOSED`. A proposed decision can be revised, creating the next
immutable version, or move once to `ACCEPTED` or `REJECTED`. An accepted decision can
only move to `SUPERSEDED`. Accepting a proposed replacement atomically supersedes the
older accepted decision in the same workspace. The older content and original
disposition actor/time remain unchanged.

Revision and disposition are deliberately separate mutations. This prevents a client
from changing the content in the same transaction that it accepts. Revision and
disposition requests both carry the current content version, and closed decisions
cannot be revised.

## Authorization and isolation

Reads use the shared workspace access rule: owner, member, organization-visible
workspace, or an explicitly elevated tenant permission. Writes require ownership,
write membership, or `workspace.manage.all`. Every lookup scopes the aggregate and its
events by `organization_id` before applying workspace access.

Content is credential-redacted before persistence. Events carry the workspace
classification and contain lifecycle metadata and fingerprints rather than full
decision rationale. Each successful mutation appends one ordered aggregate event,
one outbox message, and one audit record in the same transaction.

## Database enforcement

PostgreSQL triggers are the final boundary when an operator, integration, or future
code path bypasses the application service:

- task updates require an exact version increment, valid transition, consistent
  completion timestamp, and immutable identity/provenance;
- decision updates enforce version sequencing, lifecycle transitions, closed-state
  disposition metadata, and immutable identity/provenance;
- decision version updates and deletes use the platform's immutable-mutation guard;
- task and decision deletes are rejected.

Application tests use SQLite for fast API coverage. Opt-in PostgreSQL integration
tests exercise the actual triggers, including direct invalid SQL updates and deletes.
A fresh-database migration check verifies the entire schema chain.

## API and clients

Tasks use `/workspaces/{id}/tasks`, `/workspace-tasks/{id}`, and the aggregate event
endpoint. Decisions use `/workspaces/{id}/decisions`, revision, accept/reject,
version-history, and event endpoints under `/workspace-decisions/{id}`. The Python and
TypeScript SDKs preserve version fields and expose all lifecycle operations.

The Workbench presents active work and decision records side by side. It provides
task filtering and legal next-state actions, decision revision/history, acceptance,
rejection, and explicit replacement. A version conflict refreshes both lists and asks
the user to confirm the latest state before retrying. The layout collapses to a single
column and horizontally scrollable decision index on narrow screens.
