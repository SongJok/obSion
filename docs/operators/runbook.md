# Operator runbook

## Production prerequisites

Obsion production requires PostgreSQL 17 with pgvector 0.8 or a compatible managed
service with the `vector` extension enabled, Redis with authentication and persistence,
S3-compatible object storage, an OIDC provider, TLS
termination, a secret manager, and an OTLP/HTTP collector. Query connectors must use a
separate read-only identity and a replica or governed query service.

Set `OBSION_ENVIRONMENT=production` and `OBSION_AUTH_MODE=oidc`. Startup fails when the
issuer, audience, or JWKS URL is missing, and production cannot fall back to the seeded
development identity. Keep database, connector, model, and object-store secrets out of
Helm values and inject them through Kubernetes Secrets backed by the organization's
secret manager.

Configure an exact HTTPS `OBSION_ALLOWED_ORIGINS` list for every Workbench origin;
production refuses `*`. Set `OBSION_AUTH_SESSION_COOKIE_NAME` only before initial
rollout or as a coordinated cutover, and size `OBSION_AUTH_SESSION_TTL_SECONDS` to the
organization’s interactive-session policy (default eight hours, maximum seven days).
`OBSION_AUTH_SESSION_RETENTION_DAYS` keeps expired/revoked metadata for operational
review before opportunistic deletion during a later login (default 30 days).
Staging and production sessions are Secure, HttpOnly, SameSite=Strict, opaque, and
server-revocable. TLS must terminate before any browser can establish one.

For local development, set a non-production-only `OBSION_DEV_BEARER_TOKEN`. Paste it
into the local Workbench login page for a one-time exchange, or send it as the Bearer
credential from REST/SDK/App Server clients. The checked-in example value is
intentionally public and must never be reused in a shared or remote environment. A
missing token or session does not resolve the seeded user.

Configure model egress with exact authorities in `OBSION_MODEL_ALLOWED_HOSTS`; HTTP is
accepted only for loopback development/test endpoints.
`OBSION_MODEL_REQUEST_TIMEOUT_SECONDS` is the per-attempt deadline. Keep
`OBSION_MODEL_FORCE_PRIVATE_FOR_SENSITIVE=true` unless an approved deployment policy
explicitly replaces that control, and bind `OBSION_MODEL_PRIVATE_PROFILE_NAME` only to
endpoints whose limits declare `private=true`. `CONFIDENTIAL` or `RESTRICTED` calls
fail closed when that Profile or endpoint is absent. Store provider secrets behind
`credential_ref`; never put them in Helm values, endpoint limits, Profile requirements,
or frontend configuration.

Create the logical `fast`, `reasoning-high`, and `private` profiles independently of
provider model IDs. An endpoint must declare every capability it actually supports:
`chat`, plus `json_mode` and/or `tool_call` where applicable. Pricing belongs in
`limits.pricing_per_million`; validate it against provider billing before enabling the
endpoint. Enable Profile fallback only across endpoints with equivalent classification,
region, private, context, and tool contracts. Every failed and successful attempt is
retained separately in `model_calls` for cost and incident review.

Set `OBSION_KNOWLEDGE_EMBEDDING_PROFILE` to a logical model profile whose bound
OpenAI-compatible endpoint declares the `embeddings` capability, supports the document
classification, and returns 1,536-dimensional vectors. If it is unset, the authorized
PostgreSQL full-text path remains available; production semantic retrieval should
configure it before ingesting the corpus. Retrieval ACL predicates execute in SQL
before either full-text or HNSW ranking.

Automation is enabled by default. Size `OBSION_AUTOMATION_WORKER_CONCURRENCY` together
with the Harness worker pool and database capacity. Keep
`OBSION_AUTOMATION_LEASE_SECONDS` comfortably above the polling interval. Every API
replica may schedule and execute workflows safely because claims use PostgreSQL row
locks and leases; do not run a separate privileged scheduler identity.

The governed Action worker is enabled with `OBSION_ACTIONS_ENABLED=true`. Size
`OBSION_ACTION_WORKER_CONCURRENCY` against provider and database capacity, keep
`OBSION_ACTION_LEASE_SECONDS` above the polling interval, and use the shared
fail-closed distributed rate limiter. Setting the flag to `false` pauses claims; it
does not relax policy or turn an approved request into a success.

Register action providers only as active HTTP capability connectors with explicit
permission grants, environment, resource selector, and exact egress authority. Use
TLS outside development and store bearer credentials as secret references. V1 binds
only `action.pr.create/close` and `action.ticket.create/close` in development or
staging. Production targets and config/restart/deploy capabilities cannot be enabled
through roles, policies, connector configuration, or environment variables.

Set memory retention and context budgets explicitly for the organization's policy.
`OBSION_MEMORY_DEFAULT_TTL_DAYS` supplies omitted expiry values,
`OBSION_MEMORY_MAX_TTL_DAYS` is the hard retention ceiling, and
`OBSION_MEMORY_MAX_CONTEXT_ITEMS` plus `OBSION_MEMORY_MAX_CONTEXT_CHARS` bound the
approved snapshots entering any Run. Defaults are 365 days, 3,650 days, 40 items, and
24,000 canonical JSON characters. Lowering a value affects future writes or capture;
it does not rewrite historical Run snapshots.

Set conversation budgets independently. `OBSION_CONVERSATION_CONTEXT_MAX_TURNS`
limits prior effective Turns, `OBSION_CONVERSATION_CONTEXT_MAX_CHARS` limits their
combined stored content, and `OBSION_CONVERSATION_CONTEXT_MAX_CHARS_PER_MESSAGE`
prevents one user or assistant message from consuming the entire budget. These
settings affect newly created Runs only; existing snapshots and replays are unchanged.

Expose `/api/v1/app-server` through a reverse proxy that supports WebSocket upgrade,
preserves `Sec-WebSocket-Protocol`, and disables response buffering. The server
requires `obsion.jsonrpc.v1`, validates browser `Origin` against
`OBSION_ALLOWED_ORIGINS`, and closes connections that exceed the initialization or
message-size boundary. Size `OBSION_APP_SERVER_MAX_SUBSCRIPTIONS` together with the
database pool: one connection multiplexes its subscriptions, but every active Run is
reauthorized on each poll. The default idempotency retention is 24 hours; it must
exceed the longest client reconnect/retry window. Expired keys are safely removed when
reused and may also be purged by routine database retention after `expires_at`; the
database trigger rejects early deletion or completed-outcome mutation.

Clients must checkpoint the last successfully processed `run_sequence`, not merely the
last received frame. A WebSocket reconnect sends it as `after_sequence`; an SSE
reconnect sends it as `Last-Event-ID` (or `after`). Delivery is at least once, so
consumers also deduplicate by immutable Event ID. A reconnect that starts from an
aggregate-local `sequence` is an operational defect.

Run cancellation is terminal and database-backed. A successful cancel response means
the Run lease was cleared, active Steps were moved to `CANCELLED`, ordered request and
terminal events were committed, and no dependent Step may begin. An already-started
provider call may return later; monitor it for honest latency/cost accounting, but any
answer, `run.completed`, new Step, or transition out of `CANCELLED` is an invariant
violation.

## Install and upgrade

1. Back up PostgreSQL and verify the last restore test.
2. Review `CHANGELOG.md`, migration SQL, policy changes, and Agent/Skill checksums.
   Classify every migration as backward compatible or maintenance-window-only before
   deployment. A table or column rename is not compatible with the old application
   unless that release ships an explicitly reviewed expand/contract bridge.
3. Build or pull images by immutable digest and scan/sign them in the deployment
   registry.
4. For a backward-compatible migration, run `alembic upgrade head` once as a deployment
   job. The Helm chart creates a pre-install/pre-upgrade migration Job and blocks rollout
   if it fails.
5. Revision `f7a1b2c3d4e5` renames `audit_records` to `audit_logs` and therefore requires
   a maintenance window. Before installing a release that crosses this revision, remove
   API endpoints from ingress, stop every worker, scale the old API Deployment to zero,
   and verify that no old process retains a database session. Run the migration only
   after quiescence; then start only the new image and restore traffic after readiness
   and audit read/write checks pass. The default Helm pre-upgrade hook does not quiesce
   old Pods and must not be used by itself for this revision. Do not create a second
   audit table or dual-write as a workaround.
6. Revision `8d3f2a1c7b90` replaces free-form user department text and single-column
   identity foreign keys. Treat it as maintenance-window-only with the same quiescence
   procedure: it rejects any pre-existing cross-organization binding instead of
   silently repairing it, then drops the legacy department column after backfill.
7. Revision `19c6b2e4a7d1` additively creates `auth_sessions`. It is compatible with the
   previous application, but the new Workbench must not receive traffic until the table
   exists. After rollout, verify that rows contain only 64-character digests, expiry,
   tenant/user binding, and revocation metadata; never query or log browser cookie
   values.
8. Roll out the API, then the Workbench. Readiness must pass before traffic shifts.
9. Verify login, safe session inspection, logout/revocation, a knowledge run, an
   authorization denial, evidence/claims, audit search, an
   JSON-RPC initialization, Run subscription reconnect from its last `run_sequence`,
   mutation retry with the same client request ID, and a manual notification-only
   automation run. If a staging
   action provider is configured, verify preflight with a non-destructive test target,
   independent approval, provider idempotency, separate rollback approval, and both
   action audit records.

Schema downgrades are an incident procedure, not an automatic rollback. If a migration
is not backward compatible, restore the backup into a new database and point the prior
application version at it after validating tenant and audit integrity.

## Health and telemetry

- `GET /health/live` proves the process event loop is responsive.
- `GET /health/ready` proves the application can query PostgreSQL.
- HTTP, SQLAlchemy, Harness, capability, policy, and model spans export through
  OTLP/HTTP when `OBSION_OTEL_EXPORTER_OTLP_ENDPOINT` is configured.
- Counters include `obsion.runs`, `obsion.capability.invocations`,
  `obsion.policy.decisions`, `obsion.policy.duration`, `obsion.approval.decisions`,
  `obsion.automation.duration`, `obsion.model.calls`, and
  `obsion.automation.executions`; governed attempts add `obsion.action.attempts` by
  action type, purpose, and outcome. Memory context capture emits
  `obsion.memory.context` with scope and selected/skipped outcomes. App Server
  connections, requests, and delivered events emit `obsion.app_server.connections`,
  `obsion.app_server.requests`, and `obsion.app_server.events` with bounded attributes.

User satisfaction is projected from current `run_feedback` records through the admin
summary. Monitor both response volume and helpful rate; do not compare percentages
across periods with materially different response counts or treat feedback as factual
answer verification.

Alert on readiness failure, run failure/cancellation rate, worker lease age, capability
timeouts, policy/rate-limit safety-service failure, approval backlog/expiry, model
latency and cost, database pool saturation, Redis errors, and missing telemetry.
Also alert on overdue enabled schedules, expired automation leases, repeated workflow
failure codes, invalid owners, and human-review backlog age.
Alert separately on old action approvals, expired action leases, provider timeout or
failure rate, rollback backlog/failure, policy denial spikes, and any attempted
production or deferred action.
Telemetry and exception messages must be sampled for secret-redaction regressions.

## Backup and recovery

Back up PostgreSQL with point-in-time recovery and encrypt backups with a separately
controlled key. Version and replicate the artifact bucket; include retention rules for
classification and legal hold. Redis is coordination state and may be rebuilt, but
persistence reduces rate-limit discontinuity during restart.

Quarterly restore tests must verify organizations, memberships, runs, ordered events,
policy decisions, approvals, audits, evidence, claims, registry versions, semantic
objects, document versions, workflow versions, schedules, automation executions,
review decisions, action requests, immutable action plans, execute/rollback approvals,
attempts, policy decisions, memory candidates, immutable Run memory snapshots,
  notification deliveries, App Server request outcomes and Run event cursors,
  workspace tasks, decision headers, and every immutable
  decision revision, plus versioned Run feedback. Restore tests must confirm that the
  action-plan, memory, workspace-task, workspace-decision, decision-version, and
  run-feedback, App Server request, and Event mutation guards are present.
Never restore production data into an environment with development authentication.

## Incident procedures

### Database unavailable

Remove unhealthy API pods from traffic, pause upgrades, restore connectivity or fail
over to the validated replica, and confirm ordered event writes before re-enabling run
workers. Do not manually mark partially executed runs complete.

### Redis unavailable

With `OBSION_RATE_LIMIT_FAIL_CLOSED=true`, new capability executions fail safely while
workspace reads continue. Restore Redis, confirm authentication and key expiry, then
replay failed runs explicitly; do not disable fail-closed behavior in production.

### Cancelled Run continues

Remove the affected worker from service and preserve its logs. Confirm the Run is
`CANCELLED`, its lease fields are empty, active Steps are `CANCELLED`, and the final
Run events are `run.cancellation_requested` followed by `run.cancelled`. Do not edit
rows or append compensating events manually. If a later Step, answer, or completion
event exists, treat it as a runtime consistency incident, retain the event/audit
history, and replay only after the implementation defect is corrected.

### Connector or model provider degraded

Disable the endpoint or connector binding in the administration plane. Existing runs
retain their pinned versions and failure events. Enable a model fallback only when its
classification, region, tool-use, and budget policy are equivalent.

### Action provider response lost or degraded

First disable the affected action connector binding to stop new preflights and claims,
then inspect the request's sealed plan, attempts, policy decisions, events, and
provider idempotency ledger. Do not edit an `ActionPlan`, change an attempt key, or
manually mark lifecycle rows complete. An expired lease is reclaimed automatically
with the same provider key, so a compliant provider returns the original result
without repeating the write.

If Obsion records `FAILED` but the external write may have committed, restore the
pinned provider and request rollback through the normal endpoint. Rollback requires a
new independent approval. If the original pinned connector cannot be restored, perform
the external remediation under the organization's break-glass process and preserve
both systems' evidence; creating a new binding does not rewrite an existing sealed
plan.

### Suspected data exposure

Disable the relevant connector binding and model endpoint, preserve audit/event data,
rotate credentials, identify affected principals/resources by correlation ID, and
follow `SECURITY.md`. Do not delete evidence or audit records during investigation.

### Automation schedules overdue

Confirm API readiness and PostgreSQL write availability, then inspect enabled schedules,
their `next_fire_at`, `last_error_code`, and current owner permissions. A disabled
schedule with an ownership error must be reassigned or have permissions restored before
an authorized operator re-enables it. Never edit `next_fire_at` or insert an execution
manually; use a manual idempotent trigger when an occurrence must be replayed.

### IM ingest rejected or bound to the wrong user

Confirm the sender is bound in the Workbench IM panel or `/api/v1/admin/im-bindings`.
The identity key is `(channel, sender_id)`, never a nickname. Unmapped senders return
`unknown_im_sender`. Vendor envelopes must include `open_id`, `senderStaffId`, or
`FromUserName`. Feishu URL verification must not create a Turn. If
`OBSION_IM_WEBHOOK_SECRET` is set, unsigned envelopes fail closed. Inspect
`local_outbox` envelopes with `obsion-im --json` and optional `--outbox` before live
delivery. Explicit `feishu-http`, `dingtalk-http`, and `wecom-http` delivery is
permitted only after `POST /api/v1/experience/im/runs/{id}/deliveries` records a
Policy-authorized receipt; generic `--deliver http` is not implemented. Loopback
`serve --listen 127.0.0.1` remains the default. Public callbacks require `--public`,
TLS files, Host allowlisting, and channel-specific security. WeCom encrypted callbacks
require Token plus EncodingAESKey; missing or invalid material fails closed.

If vendor HTTP is degraded, disable that explicit transport and preserve its delivery
receipt. Do not retry with a generic URL or change an existing receipt. Validate token
scope, pinned origin, redacted error, and vendor request id before restoring traffic.
For the full support and rollback matrix, use the
[0.75.0-dev release notes](../release/0.75.0-dev.md).

### Live Feishu validation

All live probes are operator-owned, opt-in, and never run in default CI. Credentials
come only from the operator process environment and are redacted from every error.

1. `make validate-feishu-live` (requires `OBSION_FEISHU_LIVE=1` plus
   `OBSION_FEISHU_APP_ID`/`OBSION_FEISHU_APP_SECRET`) runs four non-sending probes:
   tenant authentication, read-only bot chat listing, nonexistent document failure
   closure, and wiki-space read/denial. Use the chat listing to discover a
   bot-member chat id; the listing is bounded to one vendor page.
2. `make validate-feishu-browse-live` (requires `OBSION_FEISHU_BROWSE_LIVE=1`)
   exercises the non-writing Capability Gateway source browse against the real
   tenant.
3. `make validate-feishu-send-live` (requires `OBSION_FEISHU_SEND_LIVE=1` plus
   credentials and an explicit `OBSION_FEISHU_LIVE_CHAT_ID`) delivers exactly one
   clearly marked probe message through the production `feishu-http` channel
   contract and asserts the vendor message id. The probe never auto-discovers a
   target, never sends without the explicit chat id, and is not a Harness Run; it
   produces no Run, Event, or Evidence rows. A skipped probe is never a passed
   validation. Unset the live environment variables after the run.
4. `make record-feishu-live-evidence` (requires `OBSION_FEISHU_LIVE=1`,
   credentials, and an `OBSION_LIVE_PROFILE` label) runs the declared live ladder
   and writes a redacted, checksummed ledger to
   `docs/release/evidence/alpha1/feishu-<profile>-live.yaml`. With
   `OBSION_FEISHU_SEND_LIVE=1` and an explicit chat id it also records the
   single-message send probe. A post-opt-in skip, a missing probe record, or a
   contract-disallowed outcome fails the recording; the candidate gate validates
   the ledgers offline and they never feed `promotion_eligible`.
5. `make record-drill-evidence` (requires `OBSION_DR_DRILL=1` and docker) runs
   the declared backup/restore drill: two throwaway pinned PostgreSQL 17
   containers are migrated and seeded through the real REST API, a
   custom-format dump is restored into the fresh target, and schema-version,
   row-count, referential-integrity, and audit-identity parity are recorded in
   a redacted, checksummed ledger at
   `docs/release/evidence/alpha1/backup-restore-drill.yaml`. Drill credentials
   are generated per run and never persisted; a failed stage fails every
   downstream check; the ledger never feeds `promotion_eligible` and the
   staging-scoped restore gate remains operator-owned.
6. `make record-artifact-drill-evidence` (requires `OBSION_DR_DRILL=1` and
   docker) runs the artifact-store drill: knowledge and file artifacts are
   seeded through the real REST API into a throwaway pinned MinIO container,
   the bucket is snapshotted into a canonical per-object SHA-256 manifest and
   restored into a fresh bucket on a second container, and key-set,
   content-checksum, metadata, and database-reference parity are recorded in a
   redacted, checksummed ledger at
   `docs/release/evidence/alpha1/artifact-store-drill.yaml`. The same
   fail-closed, credential, and promotion-neutral rules apply, and the
   staging-scoped restore remains a separate operator gate.


### Vendor Knowledge sync rejected or incomplete

Confirm the active connector type, `credential_ref`, exact egress origin,
`knowledge.write` permission, connector grants, rate limit, and sync budget. Missing
ACL must fail closed; do not replace it with organization-wide access. Budget
exhaustion is an explicit failed sync, not permission to silently truncate. Disable
the connector before investigating upstream scope or provenance mismatch and retain
existing DocumentVersions, Evidence, Events, and Audit.

### Operator Capability outcome is UNKNOWN

`OBSION_OPERATOR_CAPABILITY_IDEMPOTENCY_RETENTION_HOURS` controls this ledger
independently from App Server retries and defaults to 168 hours. Set it longer than
the maximum incident detection and connector reconciliation window before rollout.
Query `/api/v1/admin/operator-invocations?status=UNKNOWN` and correlate its request,
CapabilityVersion, Connector, PolicyDecision, and Audit. Do not edit the ledger or
reuse the same request UUID: exact retry remains fail-closed. Inspect the connector's
source object and the Knowledge Document/DocumentVersion lineage to determine whether
the original attempt committed. Only after connector-specific reconciliation may an
operator submit a new request UUID. A new UUID is a new operation, not proof that the
unknown attempt failed. Retain both Audit trails in the incident record.

## Routine maintenance

- Review expired approvals and memory candidates weekly. Investigate L3 memory-write
  denials, stale candidates, approaching retention limits, and unusual context-volume
  telemetry; never bypass expiry by editing a Run snapshot.
- Review pending action approvals, failed/rollback-failed actions, and provider
  idempotency retention weekly; retention must exceed the longest action recovery and
  audit window.
- Review blocked and overdue workspace tasks plus old proposed decisions weekly.
  Resolve them through normal lifecycle endpoints; never repair collaboration state
  with direct SQL or rewrite a decision version.
- Review needs-improvement reasons and response volume weekly. Handle revisions
  through the feedback endpoint; never edit or delete feedback directly, and never
  use a rating to override Evidence or Critic results.
- Review policy denials, rate-limit changes, connector health, and cost anomalies daily.
- Review UNKNOWN Operator Capability invocations daily and reconcile them before
  their retention expires; never convert UNKNOWN to FAILED by direct SQL.
- Re-run version-pinned evaluations before promoting agents, skills, prompts, models,
  semantic definitions, or capability schemas. Bind every Golden Dataset `run_ref` to
  a terminal candidate Run, use a completed exact-snapshot baseline, and stop the
  release when `gate_passed` is false. CI also runs `uv run obsion validate-eval-gates`
  and `uv run obsion scan-secrets`.
- Follow [backup/restore](backup-restore.md) and [upgrade](upgrade.md) before a
  production cutover. [SLO targets](slo.md) are engineering defaults, not a signed SLA.
  Day-two procedures: [deployment](deployment.md), [administrator](administrator.md),
  [agents and skills](agents-and-skills.md), and [incident](incident.md).
- Retire old versions only after retained runs no longer require replay against them.
- Build release artifacts only from a revision where `make check` passes: run
  `make release-artifacts`, then `make validate-release-artifacts`, and review
  `dist/release/<version>/artifact-manifest.json` (hashes, image identifiers,
  validation steps) before any promotion decision. Outputs stay local; external
  publication, signing, and CVE gating are separate operator/CI-owned steps.
- CI additionally runs `make validate-release-candidate` and retains
  `release-candidate-report.json`. Confirm all 37 requirement rows are mapped, every
  artifact and clean-room step is present, and the report names every pending operator
  gate. Use `--require-promotion-eligible` only after real staging/UAT, restore,
  signature/CVE, identity/secrets, human approval, and publication evidence has been
  reviewed; never turn a pending gate green with placeholder files.
