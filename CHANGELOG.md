# Changelog

All notable changes are documented here. The format follows Keep a Changelog and the
project follows Semantic Versioning.

## [Unreleased]

### Added

- Phase 19 IncidentAgent evidence fusion: ordered baseline/anomaly/dimension/deployment/
  log/diff investigation plans, deterministic Top1/Top3 candidate root causes, bounded
  evidence timelines and conflict retention, two-distinct-Evidence-type Claim gating,
  independent verification metadata in answer Artifacts, and Golden Dataset assertions;
  no repair, restart, configuration write, deployment write, or auto-PR path.
- Phase 20 independent deterministic Critic covering evidence sufficiency, question coverage,
  temporal and metric-definition consistency, SQL read-only reliability, incident alternatives,
  conflict reason codes, immutable verification assessments, Claim results, Evidence links and
  pairwise conflicts; failed or policy-less verification is withheld. Model endpoint
  administration exposes only credential presence, never credential references. Incident
  candidates use an explicit evidence-pair priority tie-break so the golden METRIC+DEPLOYMENT
  hypothesis remains Top1 when multiple candidates reach the confidence ceiling. Agent and
  Skill manifests now recursively reject database DSNs, endpoints, credential fields, inline
  secrets, and private keys before their immutable specs can enter model context.

- Phase 17 read-only observability connector slice: bounded metric query/compare/anomaly,
  log search/aggregate, and deployment listing operations; `observability.v1` HTTP
  response normalization into unified `ObservabilityEvent` Evidence; stable upstream,
  timeout, malformed-response, and operation-boundary errors; and planner operation
  metadata with no write or trace-dashboard path.
- Phase 18 read-only Git/change connector slice: bounded commit/diff/history,
  deployment-to-commit, and code-search operations; `engineering.v1` response
  normalization into CODE/DEPLOYMENT Evidence; repository allowlists, patch redaction,
  stable dependency errors, and no auto-PR or deployment-write path.
- Phase 9 policy and capability execution hardening: structured WHO/WHAT/RESOURCE/
  CONTEXT/RISK decisions with ALLOW/MASK/ASK/DENY effects, no-permission elevation
  prevention, L5 and side-effect denials, tenant/environment-safe Gateway resolution,
  connector grants, AgentSpec capability/risk rechecks, fail-closed rate limits,
  timeout-bounded credentialed execution, durable approvals, typed errors, and
  zero-executor blocked-path coverage.
- Phase 10 accountability and privacy boundaries: canonical AuditLog dimensions for
  agent/model/capability/resource/policy/risk/result/latency, transactional Run
  completion and failure audits, tenant-scoped audit projections, prompt-safe Turn
  persistence, assignment/Bearer/URI/private-key redaction, and deterministic
  read-only Replay without model, connector, network, or credential re-entry.
- Phase 11 Evidence Fabric and Claim boundary: one redacted, deterministic Evidence
  contract for Gateway and attachment producers, confidence/lineage/permission
  normalization, Claim-to-Evidence verification, no-evidence high-confidence rejection,
  and direct Claim-to-Evidence inspection navigation.
- Phase 12 Knowledge pipeline hardening: versioned parser/chunk metadata, explicit
  tenant/document ACL propagation to current chunk grants, identical-content ACL
  rebinds, pre-ranking authorization with deny precedence, Model Gateway embeddings in
  PostgreSQL/pgvector, and zero-recall protection for unauthorized documents.
- Phase 13 KnowledgeAgent vertical path: internal specialist routing with an immutable
  `knowledge-qa` Skill snapshot, L1 capability narrowing, deterministic citation blocks
  linked to DOCUMENT Evidence, substantive-evidence Claim filtering, and explicit
  “不知道” answers when authorized evidence is absent; added a 20-case KnowledgeAgent
  Golden Dataset with ACL denial cases.
- Phase 14 semantic catalog hardening: organization-scoped Entity, Relation, and
  BusinessRule administration, versioned semantic definitions and TimeDefinitions,
  tenant-safe Synonym targets, stable Metric/Dimension resolution, deterministic
  logical-plan SQL compilation, duplicate/cross-table dimension rejection, and explicit
  unregistered-metric refusal.
- Phase 15 SQL safety hardening: AST-gated SELECT/WITH/EXPLAIN validation, explicit-LIMIT
  mode, source/global row bounds, deterministic scan-budget estimates plus PostgreSQL
  EXPLAIN preflight, read-replica/primary fail-closed checks, row-policy predicates,
  DataColumn mask/hash output handling, auditable `sql.explain`, and Python/TypeScript
  SDK parity.
- Phase 16 DataAgent vertical slice: metric-bearing decline questions stay on the governed
  DataAgent/analytics Skill, DATA-only capability planning, metric-definition Evidence
  lineage, SQL/table/trend-chart artifacts, and temporal Vega-Lite metadata bound to the
  Evidence and query fingerprint.
- Phase 6 provider-neutral Model Gateway completion, JSON, and tool-call contracts;
  typed `fast`/`reasoning-high`/`private` Profile administration; tenant,
  classification, provider, region, context, and capability routing; fail-closed
  private overrides for confidential/restricted input; schema-validated normalized
  tool calls; Profile-scoped endpoint fallback; and honest per-attempt token, latency,
  cost, fingerprint, and outcome accounting in `model_calls`, with Agent/frontend
  provider-name boundary tests and operator configuration.
- Phase 5 single-entry, responsive three-column Workbench with a dedicated login page,
  real Principal identity display and revocable logout; opaque database-backed browser
  sessions shared by REST and App Server, digest-only persistence, HttpOnly/Secure/
  SameSite cookie controls, unsafe-request Origin enforcement, mobile overlay drawers,
  no page-level horizontal scrolling, and real Turn-to-Plan/Step/Event/Cost acceptance
  coverage across SQLite, PostgreSQL, static UI contracts, and browser verification.
- Phase 4 exact Run state graph and durable streaming/cancellation contract with
  exhaustive transition tests, WebSocket reconnect across distinct connections, SSE
  `Last-Event-ID` support, schema-governed answer/tool events, atomic terminal cancel,
  Run-before-Step lock ordering, active-Step cancellation, late-completion protection,
  ordered cancellation Events, audit, and a blocking dependent-Step acceptance test.
- Phase 2 identity foundation with explicit development bearer authentication, a
  shared protected-API authentication dependency, hierarchical organization-scoped
  departments, six immutable system-role baselines, reserved custom-role boundaries,
  Workspace repository isolation, and PostgreSQL composite tenant foreign keys with
  reversible backfill and adversarial migration tests.
- Phase 1 machine-readable Event and error contracts with versioned JSON Schemas,
  immutable schema digests, full production-producer coverage analysis, a single-Event-
  protocol architecture guard, frozen REST error envelopes, canonical PostgreSQL table
  checks, and a reversible data-preserving `audit_records` to `audit_logs` migration
  gate.
- Unified `obsion.jsonrpc.v1` App Server over WebSocket/JSON-RPC 2.0 with negotiated
  initialization, shared OIDC/development authentication, Origin and frame limits,
  Thread/Turn/Run/Approval/Artifact methods, multiplexed reauthorizing subscriptions,
  domain-named realtime notifications, durable principal-scoped mutation idempotency,
  cross-aggregate monotonic Run cursors, PostgreSQL concurrency/mutation guards,
  Python and TypeScript clients, and Workbench streaming with REST reconciliation;
  transport adapters are statically prohibited from importing persistence, Harness,
  or Model Gateway layers.
- Immutable, budget-bounded prior-Thread context captured with each new Run, including
  frozen fork lineage, temporal completed-answer selection, trust separation for
  collaborator input, classification propagation, deterministic replay fingerprints,
  PostgreSQL mutation guards, inspection API/SDKs, and a Workbench context panel.
- Complete Thread lifecycle management with transactional create/archive/resume/fork
  events and audits, manual active-Run archive guards, fork-induced source read-only
  semantics, explicit source resume, cursor-readable history, one-Turn/multiple-Run
  replay, and frozen fork-point history that excludes later parent Turns while
  supporting nested forks; includes tenant-isolated API tests, Python/TypeScript SDK
  parity, and responsive Workbench archive, restore, branch, and inspection controls.
- Governed semantic metric discovery with complete definitions, tenant-isolated
  read-only source/table/metric lineage, Python/TypeScript SDK methods, and responsive
  Workbench definition and lineage panels.
- Direct Claim-to-Evidence navigation in the runtime inspector, preserving the
  evidence source, resource, observation time, confidence, and normalized content.
- Versioned terminal-Run feedback with redacted improvement reasons, idempotent
  writes, PostgreSQL mutation guards, ordered Run events, audits, tenant-scoped
  satisfaction summaries, Python/TypeScript SDKs, and accessible Workbench controls
  for copy, deterministic playback, and rating.
- Workbench context selection for readable workspace artifacts, reusing the governed
  attachment authorization, parsing, redaction, Evidence lineage, and replay path;
  non-functional user and context affordances were removed or completed.
- Governed workspace collaboration with optimistic-concurrency tasks, database
  status guards, active-member assignment, optional Run provenance, immutable
  checksummed decision revisions, accept/reject disposition, atomic supersession
  lineage, ordered events, audits, Python/TypeScript SDKs, and responsive Workbench UI.

- Governed TURN, SESSION, WORKSPACE, and USER_PREFERENCE memory with exact owner
  authorization, policy-linked candidates, classification floors, redaction,
  deduplication, bounded TTLs, approved-context budgets, immutable Run snapshots,
  deterministic replay, runtime inspection, telemetry, SDKs, and PostgreSQL mutation
  guards.
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
