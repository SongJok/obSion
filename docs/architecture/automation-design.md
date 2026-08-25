# Automation architecture

## Purpose and boundary

Obsion Automate is the Phase 6 control plane for repeatable, read-only operational
work. It turns a governed analysis that already runs through the Harness into a
deterministic workflow that can be triggered manually or on a schedule. It does not
open or invoke the Phase 7 action boundary: automation nodes remain read-only and
cannot create PRs or tickets. Those operations use a separate user-authored action,
immutable plan, independent approval, and Action worker. Production mutation,
configuration change, restart, and deployment remain globally unavailable.

The automation layer owns orchestration, timing, concurrency, operational ownership,
and delivery state. It never implements a second agent runtime and it never gives a
scheduler a privileged service identity that bypasses the owner.

## Domain model

- `WorkflowDefinition` is workspace-scoped mutable metadata. It owns lifecycle,
  concurrency policy, timeout, notification policy, classification, and accountable
  owner.
- `WorkflowVersion` is an immutable, checksummed DAG specification. Publishing pins
  one version as active without rewriting older versions.
- `WorkflowSchedule` pins a published workflow version, a five-field cron expression,
  IANA timezone, misfire policy, and input payload. The next fire time is materialized
  so claiming a due schedule is an indexed row-lock operation.
- `AutomationExecution` is one manual or scheduled orchestration instance. It has a
  durable idempotency key, deadline, lease, trigger metadata, and terminal summary.
- `AutomationStepExecution` persists every deterministic node and links analysis
  nodes to ordinary Harness `Run` records.
- `NotificationDelivery` is a durable, recipient-scoped in-app delivery with read
  state and an idempotency key.

All rows carry the organization boundary. Workspace access is checked before any
resource is returned, and repository queries always include organization scope.

## Workflow contract

A workflow version contains one to fifty nodes. Node identifiers are stable inside a
version, dependencies must exist, and the graph must be acyclic. The supported Phase 6
nodes are deliberately small:

- `ANALYSIS` creates an ordinary background Thread, Turn, and Run. Its prompt is
  rendered with a constrained scalar placeholder grammar. Artifacts from completed
  dependencies are attached to the downstream Turn.
- `HUMAN_REVIEW` pauses the execution until an authorized reviewer approves or
  rejects it. The decision and reason are audited.
- `NOTIFICATION` creates an in-app delivery for the accountable owner after its
  dependencies complete.

Independent ready analysis nodes are submitted together and the existing Run worker
executes them concurrently. This preserves the goal blueprint's split: workflows own
deterministic order while Agents own non-deterministic judgment.

## Scheduling and exactly-once creation

Every API replica may run the scheduler. A due row is claimed with
`SELECT ... FOR UPDATE SKIP LOCKED`; its `next_fire_at` is advanced in the same
transaction that inserts the execution. A unique organization/idempotency key and a
unique `(schedule_id, scheduled_for)` constraint prevent duplicate execution creation
after retries, replica races, or process restarts.

Five-field cron expressions and IANA timezones are validated at write time. `SKIP`
discards occurrences outside the configured grace window. `FIRE_ONCE` collapses all
missed occurrences into one execution, so outages cannot create an unbounded catch-up
storm.

## Execution leases and concurrency

Executions are claimed with row locks and renewable leases. A crashed worker can be
reclaimed after lease expiry. Workflows declare one of three policies:

- `FORBID` records a skipped execution while another execution is active.
- `ALLOW` permits execution up to the declared maximum concurrency.
- `REPLACE` cancels older active executions and requests cancellation of their child
  Runs before admitting the replacement.

The workflow deadline is independent of child Run deadlines. On deadline or explicit
cancellation, pending nodes are cancelled and active child Runs receive the ordinary
Run cancellation request.

## Identity, notifications, and audit

Scheduled executions reload the workflow owner from the identity store at fire time.
The owner must still be active, retain workspace write access, and retain
`automation.trigger`. If ownership becomes invalid, the schedule is disabled with a
durable error instead of silently escalating to a platform administrator.

Phase 6 notifications are production in-app deliveries. They have durable delivery
and read state and are visible only to the recipient (or an organization-wide
notification administrator). External chat, email, and incident-system delivery must
later be implemented as registered idempotent capabilities so policy, secret
brokering, egress controls, rate limits, and audit remain mandatory.

Every definition, version, publication, schedule change, trigger, concurrency skip,
review, cancellation, completion, failure, and notification is represented by an
append-only event and an audit record. Workflow versions use a dedicated PostgreSQL
guard that permits the one-time unpublished-to-published transition while rejecting
content updates and all deletes.

## Failure model

- Invalid DAGs, cron expressions, timezones, templates, and version references are
  rejected before persistence.
- A failed analysis node fails the workflow and cancels nodes that can no longer run.
- Worker exceptions are converted to a stable failure code; raw exception details and
  credentials never enter user-visible payloads.
- Failure and success notifications are idempotent and cannot multiply after lease
  recovery.
- Scheduler and automation loops are independently bounded so a slow workflow cannot
  block schedule claiming.

## Acceptance gates

- Cross-organization workflow, schedule, execution, review, and notification access is
  impossible at repository and API boundaries.
- A schedule race creates at most one execution for one occurrence.
- Workflow version rows reject update and delete in PostgreSQL.
- Concurrency policies are covered by transactional tests.
- Background analysis produces an ordinary replayable Harness Run with evidence and
  artifacts.
- Invalid ownership fails closed.
- OpenAPI, Python SDK, TypeScript SDK, Workbench, Compose, Helm, migration, and
  observability contracts are updated in the same phase.
