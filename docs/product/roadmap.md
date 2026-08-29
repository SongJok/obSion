# Delivery roadmap

The phases are architecture increments, not disposable prototypes. Each phase leaves production-quality contracts, migrations, tests, telemetry, and documentation.

## Phase 0: Foundation

Deliver the App Server, Harness lifecycle, Thread/Turn/Run/Event store, model gateway, registries, Capability Gateway, identity, policy, approval, audit, Evidence model, artifacts, and Workbench. The first vertical path is user input to agent plan to authorized capability to evidence-backed answer.

## Phase 1: Knowledge

Deliver versioned ingestion, supported parser contracts, structure-preserving chunks, document/chunk ACL inheritance, retrieval authorization, reranking, citations, and KnowledgeAgent evaluation cases.

## Phase 2: Data

Deliver metadata ingestion, semantic catalog, metric governance, historical-query signals, logical planning, dialect compilation, AST validation, query policy, read-only gateway, table/chart artifacts, and DataAgent evaluation.

## Phase 3: Engineering and incidents

Deliver Git, CI/CD, log, metric, trace, configuration, and Kubernetes read-only connectors; normalized observability events; deployment-to-commit lineage; EngineeringAgent and IncidentAgent.

## Phase 4: Verified answers

Strengthen evidence normalization, atomic claims, conflict detection, confidence calibration, independent critic execution, bounded replanning, and evidence coverage gates.

## Phase 5: Workspace

Complete files, artifacts, reports, dashboards, code and SQL views, evidence navigation, runtime timeline, costs, memory inspection, and collaboration.

Thread lifecycle is delivered through transactional create/archive/resume/fork
events and audits, explicit parent/Turn lineage, manual active-Run archive protection,
fork-induced source read-only behavior, explicit resume, one-Turn/multiple-Run replay,
cursor-readable inspection, frozen fork-point history including nested forks, SDK
contracts, and responsive Workbench controls.

Conversation continuity is delivered as a bounded immutable Run input captured at
Turn creation, with fixed fork lineage, temporal answer selection, collaborator trust
isolation, classification propagation, deterministic replay, API/SDK contracts,
PostgreSQL mutation guards, and a Workbench context inspector.

Memory inspection is delivered through a governed four-scope lifecycle, policy and
classification enforcement, bounded TTLs, authorized Harness context capture,
immutable Run snapshots, deterministic replay, API/SDK contracts, and a dedicated
Workbench inspector.

Shared collaboration is delivered through versioned workspace tasks, legal status
transitions, active-member assignment, optional Run provenance, immutable checksummed
decision revisions, explicit accept/reject disposition, atomic supersession lineage,
ordered events, audit records, Python/TypeScript SDKs, and a responsive Workbench view.

User satisfaction is delivered as tenant-scoped, versioned terminal-Run feedback with
redacted improvement reasons, ordered Run events, audit records, database mutation
guards, Python/TypeScript SDKs, a current-record administration projection, and real
copy/playback/rating controls in the responsive conversation view.

## Phase 6: Automation

Add deterministic workflows, schedules, background runs, notifications, recurring analyses, concurrency policy, and operational ownership.

Delivered with immutable checksummed DAG versions, cron/IANA scheduling, idempotent
PostgreSQL claims and leases, current-owner re-authorization, `FORBID`/`ALLOW`/`REPLACE`
concurrency, ordinary Harness child Runs, human review gates, in-app delivery, SDKs,
Workbench controls, telemetry, and operator procedures.

## Phase 7: Governed actions

Open change execution incrementally after read-path maturity. The first delivered
release includes PR generation and ticket creation in development/staging through a
dedicated Action Gateway, immutable preflight plans, independent execute and rollback
approvals, pinned provider contracts, stable idempotency keys, durable worker leases,
compensating actions, notifications, telemetry, and audit records.

Configuration changes, service restarts, deployments, production targets,
non-idempotent writes, and destructive operations remain server-side denials. They
require separate future release gates; installing a connector or granting a role does
not enable them.

## Phase 20: Governance and production hardening

Delivered as a continuation of the vertical paths: independent deterministic Critic rules,
immutable verification assessments and conflict links, credential-safe management projections,
approval decisions, and replay of the complete verification graph. Production write/deploy/
restart boundaries remain fail-closed. Provider, egress, and retention sign-off remains an
operational prerequisite and is tracked as `PENDING` without blocking development.

## Quality gates

Every phase requires API/schema compatibility checks, database migrations, unit and
integration tests, tenant-isolation tests, threat-model cases, OpenTelemetry coverage,
operator documentation, and automated evaluation datasets for changed agent behavior.
Committed Golden Datasets are validated in CI; candidate releases bind their real
terminal Runs, compare against an exact-snapshot baseline, and must pass configured
pass-rate, regression-rate and named-score gates.
