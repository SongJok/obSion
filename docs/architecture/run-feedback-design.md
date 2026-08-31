# Run feedback and user-satisfaction architecture

## Purpose and boundary

Obsion treats user satisfaction as governed product evidence, not an ephemeral UI
signal. An authenticated principal may keep one current feedback record for a
terminal Run they can read. Feedback is deliberately separate from verification:
`HELPFUL` never changes Claim confidence, Evidence coverage, Critic status, or the
Run result.

The first contract has two ratings: `HELPFUL` and `NEEDS_IMPROVEMENT`. The latter can
carry a short reason so operators can distinguish relevance, evidence, clarity, and
workflow problems without collecting a second ungoverned comment store.

## Persistence and invariants

`RunFeedback` is tenant-owned and uniquely identified by
`(organization_id, run_id, user_id)`. It stores the current rating, redacted reason,
optimistic version, and timestamps. The application locks the Run before mutation so
concurrent first submissions cannot bypass the single-record contract.

PostgreSQL independently enforces:

- positive versions and one record per principal and Run;
- immutable organization, Run, user, and creation identity;
- an exact version increment for every revision;
- rejection of direct deletion.

Create and revision commit the feedback row, a `run.feedback.recorded` or
`run.feedback.revised` event, an outbox message, and a redacted audit record in one
transaction. Feedback events use the Run aggregate, preserving the same contiguous
sequence consumed by polling and resumable event streams. Repeating identical
content is idempotent and emits no duplicate event.

## Authorization and data handling

The normal workspace read boundary gates both reading and recording feedback. A
caller cannot infer that a Run exists in another tenant or inaccessible workspace.
Only the caller's own feedback is returned; another user's reason is never exposed
through the Run endpoint or administrative summary.

Reasons pass through the shared secret/token redactor before persistence. Events,
outbox messages, telemetry, and aggregate administration expose only whether a
reason exists. A changed record requires the exact version last read; stale writes
return a stable conflict and require an explicit refresh.

## API and projections

- `GET /api/v1/runs/{run_id}/feedback` returns the caller's current record or `null`.
- `PUT /api/v1/runs/{run_id}/feedback` creates or revises it. Revisions require
  `expected_version`.
- `GET /api/v1/admin/feedback/summary` requires `audit.read` and returns current
  tenant counts plus `helpful_rate = helpful / total`; the rate is `null` when no
  feedback exists.
- `GET /api/v1/admin/slo` includes the same satisfaction buckets plus the other
  goal.txt core rates from PostgreSQL. It does not invent p95.

The administrative projection is calculated from current durable records, so a
revision moves one response between buckets instead of inflating the denominator.
Python and TypeScript SDKs preserve these contracts.

## Workbench behavior

Terminal answers expose real copy, deterministic playback, helpful, and needs-
improvement controls. Copy reports browser denial instead of claiming success.
Playback is labelled as historical snapshot playback and never implies fresh access
to external systems. A needs-improvement rating collects a bounded reason and shows
the persisted state after saving. Thread loading restores the current principal's
feedback for every visible Run, while the administration console reports the
tenant-level satisfaction rate and response count.

## Verification and operation

Acceptance covers lifecycle, idempotency, redaction, stale-version conflict,
contiguous Run events, tenant isolation, administrative projection, SDK request
contracts, database mutation guards, responsive UI behavior, migration from an empty
database, and OpenAPI drift. Operators monitor response volume with the rate: a high
percentage based on too few responses is not a release signal by itself.
