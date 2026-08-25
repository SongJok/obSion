# Changelog

All notable changes are documented here. The format follows Keep a Changelog and the
project follows Semantic Versioning.

## [Unreleased]

### Added

- Evidence-producing evaluation gates with explicit routing, SQL-policy and recorded
  Run evaluators; immutable per-case results; Agent/Skill/Capability/Prompt/model
  snapshots; Golden Dataset Run bindings; baseline comparisons; and CI validation.
- Deterministic Run snapshot replay with stable fingerprints, pinned Capability
  version IDs, remapped Evidence/Claim/Artifact lineage, replay-safe event envelopes,
  and no Model or Connector re-execution.
- Durable Workspace, Thread, Turn, Run, Step, Event, Artifact, Evidence, and Claim
  lifecycles with replay and resumable event streaming.
- Python control plane with Capability, Model, Policy, Approval, Credential, Audit,
  Knowledge, Semantic Data, Memory, and Evaluation services.
- Governed knowledge, analytics, and incident-investigation execution paths.
- Next.js Workbench and administrative console with responsive runtime inspection.
- PostgreSQL/pgvector migrations, OpenTelemetry integration, SDKs, Compose, Helm, and
  CI assets.
- Pinned token/cost/step budgets, bounded read-only recovery replanning, governed
  secret references, hybrid ACL-before-ranking retrieval, and rich data artifacts.
- Phase 6 automation control plane with immutable DAG versions, cron/IANA schedules,
  PostgreSQL-backed idempotent execution leases, concurrency policies, recurring
  Harness analysis, human review gates, in-app notifications, SDKs, and Workbench UI.
- Phase 7 governed-action control plane for PR and ticket operations in
  development/staging, with immutable checksummed plans, non-self execution and
  rollback approvals, pinned L3 idempotent HTTP providers, durable attempt leases,
  safe recovery after lost responses, compensating actions, policy/audit evidence,
  notifications, Python and TypeScript SDKs, and a Workbench action center.
- Server-side denials for all production actions and deferred configuration, restart,
  and deployment action types; generic capability invocation remains read-only.
