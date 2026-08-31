# Changelog

All notable changes are documented here. The format follows Keep a Changelog and the
project follows Semantic Versioning.

## [Unreleased]

### Added

- Phase 86 Alpha.1 artifact-store drill evidence: a declared
  `ArtifactDrillEvidenceLadder` contract binds eight ordered checks to a real
  bucket snapshot/restore cycle, and `obsion record-artifact-drill-evidence`
  (plus `make record-artifact-drill-evidence`) migrates a throwaway pinned
  PostgreSQL 17 container with Alembic, seeds knowledge and file artifacts
  through the real REST API into a throwaway pinned MinIO container via the
  production `MinioObjectStore` write path, snapshots the bucket into a
  canonical per-object SHA-256 manifest, restores from snapshot bytes into a
  fresh bucket on a second MinIO container, and verifies key-set,
  content-checksum, metadata, and database-reference consistency in a
  redacted, SHA-256-checksummed `ArtifactDrillEvidenceLedger`. Classification
  is fail-closed: once a stage fails, every downstream check is `failed`;
  drill credentials never leave process memory. A new
  `OBSION_OBJECT_STORE_BACKEND` setting selects the artifact backend
  explicitly (default `auto` preserves existing behaviour). The candidate
  contract's `drillEvidence` section becomes a `ladders` list validated per
  ladder kind (`drill_evidence_ledgers: 2`, `drill_evidence_checks: 16`), and
  recorded evidence never feeds `promotion_eligible`; the staging-scoped
  `backup-restore-drill` operator gate remains PENDING. One real ledger was
  recorded (8/8 checks passed). Version is `0.86.0-dev`.

- Phase 85 Alpha.1 backup/restore drill evidence: a declared
  `DrillEvidenceLadder` contract binds eight ordered checks to a real
  dump/restore cycle, and `obsion record-drill-evidence` (plus
  `make record-drill-evidence`) migrates a throwaway pinned PostgreSQL 17
  container with Alembic, seeds a governed Harness scenario through the real
  REST API, restores a custom-format `pg_dump` into a fresh target, and
  verifies schema-version, 89-table row-count, referential-integrity, and
  audit-identity parity in a redacted, SHA-256-checksummed
  `DrillEvidenceLedger`. Classification is fail-closed: once a stage fails,
  every downstream check is `failed`; drill credentials never leave process
  memory. The candidate gate validates the new `drillEvidence` section offline
  and recorded evidence never feeds `promotion_eligible`; the staging-scoped
  `backup-restore-drill` operator gate remains PENDING. One real ledger was
  recorded at revision `d4c6650` (8/8 checks passed, total 27.5s).

- Phase 84 Alpha.1 live-tenant evidence ledger: a declared
  `LiveEvidenceLadder` contract binds six Feishu probes to the existing opt-in
  pytest nodes, and `obsion record-live-evidence` (plus
  `make record-feishu-live-evidence`) runs the ladder against a real tenant and
  writes redacted, SHA-256-checksummed `LiveEvidenceLedger` files under
  `docs/release/evidence/alpha1/`. Classification is fail-closed: post-opt-in
  skips, missing probe records, and contract-disallowed outcomes are `failed`;
  credential-shaped values and forbidden keys are rejected at record and
  validation time. The candidate gate validates the new `liveEvidence` section
  in every mode (schema, checksum, union coverage) without vendor traffic, and
  recorded evidence never feeds `promotion_eligible`. Two real ledgers were
  recorded at revision `467fe95`: the read-only profile shows tenant
  authentication passed with correct scope denials, and the agent profile adds
  chat discovery plus a single live delivery
  (`om_x100b666298f33ca8c2b188749811eb0`) through the production `feishu-http`
  channel. Version is `0.84.0-dev`.

- Phase 83 Alpha.1 release-candidate hardening: artifact builds now require a clean
  git revision, mark diagnostic dirty builds as ineligible, remove stale Maven JARs,
  and record container SHA-256 identities. A machine-validated
  `ReleaseCandidateGate` maps all 37 V1 requirement rows to the exact twelve shipped
  artifact identities and repository evidence, enforces eleven clean-room steps, and
  keeps six external promotion prerequisites explicitly `PENDING`. CI now depends on
  quality/migration/Java/Helm gates, builds and validates the complete candidate,
  scans the exact images, and retains the artifact manifest and candidate report for
  fourteen days without publishing. Version is `0.83.0-dev`.

- Phase 82 Alpha.1 artifact build and clean-room installation: `scripts/release_artifacts.py`
  (standard library only, fixed argument lists, bounded timeouts, no credential
  access) builds the four Python distributions via `uv build`, the `@obsion/sdk`
  tarball via `npm run build` + `npm pack`, the Java SDK JAR via `./mvnw package`
  inside the pinned `eclipse-temurin:21-jdk` container, and the
  `obsion-control-plane`/`obsion-web` images from the committed Dockerfiles. All
  outputs land in the gitignored `dist/release/<version>/` tree with SHA-256
  hashes, image identifiers, and the git revision recorded in
  `artifact-manifest.json` (`externallyPublished: false`). `validate` re-verifies
  hashes, installs the wheels into a fresh temporary venv with import/CLI smokes,
  installs the Node tarball into a temporary npm prefix, lists the JAR's
  `dev/obsion` classes, and smoke-runs both images locally. `make
  release-artifacts` and `make validate-release-artifacts` expose the flow; the
  Java SDK version aligns to `0.1.0` with the other packages; the release-notes
  validator now permits an empty `vendors` list for artifact-only releases. The
  `0.82.0-dev` machine/human release contracts become the CLI default. Version is
  `0.82.0-dev`.

- Phase 81 Feishu live reply validation: `FeishuClient.list_chats` adds read-only,
  single-page-bounded bot chat discovery with fail-closed item validation and
  credential redaction. `make validate-feishu-live` now runs four non-sending
  probes. A new strict `feishu_send_live` marker and `make validate-feishu-send-live`
  deliver exactly one explicitly marked probe message through the production
  `feishu-http` channel contract after `OBSION_FEISHU_SEND_LIVE=1`, environment
  credentials, and an explicit `OBSION_FEISHU_LIVE_CHAT_ID` are present. The send
  probe never auto-discovers a target, never counts a skip as a pass, and creates
  no Run/Event/Evidence rows. The `0.81.0-dev` machine/human release contracts
  become the CLI default, and the `0.80.0-alpha.1` manifest is frozen as a static
  historical contract whose live-tree evidence was validated at the Alpha.1
  candidate commit. `FeishuClient` now parses HTTP 400 business envelopes before
  status classification and classifies 401/403 and documented denied vendor codes
  as `FeishuDeniedError` with bearer-token redaction. Live tenant runs validated
  all four non-sending probes, the Gateway browse, and one operator-run
  end-to-end delivery (vendor message id recorded in the Phase 81 report).
  Version is `0.81.0-dev`.

- Phase 80 Alpha.1 repository release contract: `0.80.0-alpha.1` binds project status,
  every Phase 1-80 report and architecture review, the exact 30-revision Alembic
  chain, CycloneDX version, vendor boundaries, rollout/rollback, and candidate-only
  publication status. Missing early reports are transparently retrospective, the
  legacy `0.75.0-dev` contract remains valid, and no tag/artifact was published.

- Phase 79 Operator Capability idempotency: L2 no-Run idempotent writes now use a
  principal-scoped, immutable two-transaction ledger. Exact retries replay terminal
  results without rate, credential, or connector work; mismatched keys conflict and
  expired attempts become UNKNOWN without automatic retry. Admin/SDK/Workbench
  projections expose reconciliation metadata but never input/result content. UUID
  request keys fail closed, current Policy is rechecked on replay, Python/TypeScript/
  Java SDKs share the projection, and ledger retention is independently configurable.
  Version is `0.79.0-dev`.

- Phase 78 Vendor Knowledge read Gateway unification: all eight source browsing GET
  routes now use the no-Run Capability Gateway through versioned
  `knowledge.source.containers` / `knowledge.source.items` contracts. Both are L1,
  side-effect-free, schema- and budget-bounded, preserve `knowledge.write` source
  management authorization, and produce Policy/Audit without Run/Event/Evidence.
  Existing vendor REST responses are unchanged. Version is `0.78.0-dev`.

- Phase 77 Vendor Knowledge write Gateway unification: all eight vendor REST
  ingest/sync endpoints now use a no-Run entry in the same Capability Gateway for
  binding, Policy, grant, schema, rate, credential, executor, masking, telemetry, and
  Audit enforcement. The entry is closed to exact L2 idempotent Knowledge writes;
  ASK fails without fabricating Approval/Run/Event/Evidence. Vendor-neutral output
  schemas add immutable document `version_id`. No API or database migration is added.
  Version is `0.77.0-dev`.

- Phase 76 Feishu live validation: `make validate-feishu-live` runs exactly three
  explicitly marked, non-sending tenant probes after required environment opt-in.
  Real Feishu HTTP 400 business envelopes are parsed before status fallback;
  missing/inaccessible document code `99992402` and wiki permission code `99991672`
  normalize to denied without leaking resource existence or credentials. No message,
  ingest, migration, new runtime, or credential-file loader is added. Version is
  `0.76.0-dev`.

- Phase 75 release-note consolidation: operator-facing `0.75.0-dev` notes and a
  machine-validated release manifest consolidate DingTalk/WeCom HTTP, WeCom AES,
  public vendor ingress, DingTalk/WeCom Knowledge, shared provenance/budgets, and
  citation UI from Phases 68-74. CI validates phase continuity, migration posture,
  pinned origins, environment-variable names, referenced documents, rollout, and
  rollback. The SBOM now reads the authoritative project-status version instead of
  retaining the Phase 25 development version. No database migration is added.

- Phase 74 Knowledge citation UI: Workbench Knowledge search and Runtime
  Inspector surface connector provenance from SearchHit / Evidence hits without
  inventing missing fields. Version is `0.74.0-dev`.

- Phase 73 Vendor Knowledge hardening: shared sync budget, provenance metadata,
  and Gateway-aligned REST rate limits for Feishu/DingTalk/WeCom/Confluence.
  Silent truncation is fail-closed. Version is `0.73.0-dev`.

- Phase 72 WeCom knowledge docs: WeCom cloud documents enter the Knowledge
  pipeline through the `wecom-docs` connector and `knowledge.ingest` /
  `knowledge.sync`. Egress is pinned to `qyapi.weixin.qq.com`. ACL is explicit or
  inherited and never invented as organization-wide. Version is `0.72.0-dev`.

- Phase 71 DingTalk knowledge docs: DingTalk cloud documents enter the Knowledge
  pipeline through the `dingtalk-docs` connector and `knowledge.ingest` /
  `knowledge.sync`. Egress is pinned to `api.dingtalk.com`. ACL is explicit or
  inherited and never invented as organization-wide. Version is `0.71.0-dev`.

- Phase 70 public DingTalk / WeCom ingress: `obsion-im serve --public` accepts
  Feishu, DingTalk, and WeCom after TLS, Host allowlist, and channel-specific
  security checks. Version is `0.70.0-dev`.

- Phase 69 WeCom AES decrypt: WeCom `Encrypt` callbacks decrypt with
  `OBSION_WECOM_ENCODING_AES_KEY` after optional Token signature checks.
  Ciphertext without EncodingAESKey still fails closed. Version is `0.69.0-dev`.

- Phase 68 DingTalk / WeCom HTTP: `obsion-im --deliver dingtalk-http` and
  `--deliver wecom-http` post Policy-authorized final answers to the pinned
  vendor OpenAPI origins. Credentials come only from `OBSION_DINGTALK_*` /
  `OBSION_WECOM_*` environment variables. Generic `--deliver http` remains
  fail-closed. Version is `0.68.0-dev`.

- Phase 67 public IM ingress: `obsion-im serve --public` hosts an HTTPS Feishu
  webhook after TLS, Encrypt Key, and Host allowlist checks. Loopback remains
  the default. Version is `0.67.0-dev`.

- Phase 66 Confluence knowledge: Confluence Cloud pages enter the Knowledge
  pipeline through the `confluence` connector. Site hosts are `*.atlassian.net`
  only. ACL is explicit or inherited from restrictions and never invented.
  Version is `0.66.0-dev`.

- Phase 65 Feishu wiki spaces: operators can list and sync a Feishu wiki space
  through `knowledge.sync`. Non-docx nodes are skipped, not pretended ingested.
  Version is `0.65.0-dev`.

- Phase 64 Feishu knowledge docs: Feishu cloud documents enter the Knowledge
  pipeline through the `feishu-docs` connector and `knowledge.ingest`. ACL is
  explicit or inherited from Feishu members and never invented. Version is
  `0.64.0-dev`.

- Phase 63 Feishu event signature: loopback webhooks verify official
  `X-Lark-Signature` headers and decrypt documented AES-256-CBC events.
  Encrypt Key stays in `OBSION_FEISHU_ENCRYPT_KEY`. Version is `0.63.0-dev`.

- Phase 62 Vendor IM HTTP: `obsion-im --deliver feishu-http` posts a
  Policy-authorized final answer to Feishu OpenAPI. Credentials come only from
  `OBSION_FEISHU_*` environment variables. Generic `--deliver http`, DingTalk,
  and WeCom HTTP remain fail-closed. Version is `0.62.0-dev`.

- Phase 61 Workspace timeline: `GET /workspaces/{id}/timeline` lists persisted
  Run Events joined through Run → Turn → Thread. It does not invent Harness
  steps. Version is `0.61.0-dev`.

- Phase 60 Workspace evidence: `GET /workspaces/{id}/evidence` lists persisted
  Evidence rows joined through Run → Turn → Thread. Greetings do not invent
  evidence. Version is `0.60.0-dev`.

- Phase 59 Workspace SQL: `GET /workspaces/{id}/sql` lists published SQL
  artifacts. The Workbench rail is read-only and does not invent warehouse rows.
  Version is `0.59.0-dev`.

- Phase 58 Workspace dashboards: Data Runs that already produced a `CHART`
  publish a `DASHBOARD` that only references those SQL/TABLE/CHART artifacts.
  Greetings and knowledge answers do not. `GET /workspaces/{id}/dashboards`
  lists the ledger. This is not a fabricated series. Version is `0.58.0-dev`.

- Phase 57 Workspace reports: evidenced Harness Runs publish a `REPORT` linked to
  the TEXT answer. Greetings do not. `GET /workspaces/{id}/reports` lists the
  ledger. This is not a dashboard fabric. Version is `0.57.0-dev`.

- Phase 56 Workspace files: FILE artifacts may occupy a governed workspace path.
  Reusing the current path increments `file_version` and supersedes the previous
  row. `GET /workspaces/{id}/files` lists the ledger. Files are not SYSTEM
  context. Version is `0.56.0-dev`.

- Phase 55 Runtime SLO projection: `GET /api/v1/admin/slo` reads success, replan,
  approval, satisfaction, evidence coverage, tokens, cost, and mean latencies from
  PostgreSQL. TTFT stays histogram-only. This is not a p95 SLA. Version is
  `0.55.0-dev`.

- Phase 54 Tool result context: Capability `EvidenceType.TOOL` rows are a separate
  untrusted `tool-result` Context Builder segment. Retrieved evidence stays on
  `evidence-bus`. Version is `0.54.0-dev`.

- Phase 53 Workspace context: each Turn pins Workspace identity and redacted
  description on `runs.workspace_context`. Identity is AGENT; description is
  UNTRUSTED_DATA. Replay copies the pin. Version is `0.53.0-dev`.

- Phase 52 Conversation compaction: older thread turns become one extractive
  `conversation-compact` segment. Recent turns stay verbatim. The ledger is pinned
  on `runs.conversation_compact`. This is not an LLM summary. Version is
  `0.52.0-dev`.

- Phase 51 Context token budget: Context Builder records KEEP / COMPRESS /
  SUMMARIZE / DROP per segment. SUMMARIZE is extractive, not a model call. The
  ledger is pinned on `runs.context_budget` and shown in the inspector. Version is
  `0.51.0-dev`.

- Phase 50 Prompt template render: pinned PromptVersion `{name}` substitution is
  schema-bound. Secret/user variable names and nested placeholders fail closed.
  Harness interpolates only governed `route`. No Jinja/eval/format. Version is
  `0.50.0-dev`.

- Phase 49 runtime Prompt pin: each Turn pins `obsion-system-policy` (plus AgentSpec
  `prompts`) onto `runs.prompt_pins`. Context Builder loads the snapshot by version
  id. Eval can pin and compare Prompt versions (`prompt_changed`). Checksum mismatch
  is `prompt_pin_mismatch`. Version is `0.49.0-dev`.

- Phase 48 Agent/Prompt versioning: Studio compare of Agent, Skill, and Prompt
  snapshots; Agent/Skill rollback via promote of a previous checksummed version;
  `traffic_split` always false; Prompt rollback denied. Evaluate remains Eval console
  pins, not a second Harness. Version is `0.48.0-dev`.

- Phase 47 Connector plugin governance: SPI connectors declare Network / Filesystem /
  Capabilities / Secrets / Risk. Static scan, HMAC-SHA256 (`OBSION_CONNECTOR_MANIFEST_KEY`),
  registry, L3+ promote (`approval.decide`), and production signature fail-closed. L5 is
  denied. No pip, importlib, binary scan, or GPG. Version is `0.47.0-dev`.

- Phase 46 Connector SDK: Python `ConnectorAdapter` SPI (`health` / `discover` /
  `execute`) hosted in-process by `ConnectorSdkRuntime`. Execute stays on the
  Capability Gateway (INTERNAL). Admin health/discover are audited and never auto-bind
  Capabilities. pip/module/url and non-empty egress fail closed. Version is
  `0.46.0-dev`.

- Phase 45 core SDKs: `packages/sdk-java` is a JDK 21 REST client of the Python
  control plane (Studio Agent/Skill, Connector create, Capability bind/invoke).
  Python and TypeScript SDKs wrap the same admin Connector and binding routes.
  This is not a Java backend or second Harness. Version is `0.45.0-dev`.

- Phase 44 WORKFLOW Gateway dispatch: a connector `workflow_id` calls
  `AutomationService.trigger_workflow` (`trigger=CAPABILITY`) in the Gateway
  transaction. Nested dispatch from an automation ANALYSIS child Run returns
  `budget_exceeded`. Temporal/Airflow remain unimplemented. Version is `0.44.0-dev`.

- Phase 43 AGENT in-process transport: Capability Gateway encodes `{agent,
  operation, input}` for `agent-development` / `obsion.development.echo`. Nested
  Harness, remote agent URLs, and non-empty egress fail closed. Version is
  `0.43.0-dev`.

- Phase 42 WORKFLOW in-process transport: Capability Gateway encodes `{workflow,
  operation, input}` for `workflow-development` / `obsion.development.echo`.
  Temporal/Airflow/url and non-empty egress fail closed. Version is `0.42.0-dev`.

- Phase 41 gRPC in-process transport: Capability Gateway encodes `{service, method,
  message}` for `grpc-development` / `obsion.development.Echo/Ping`. host/port/tls and
  non-empty egress fail closed. Version is `0.41.0-dev`.

- Phase 40 sandbox runtime pin: AgentSpec sandbox is normalized, written onto
  `run.plan.sandbox`, and enforced at the Capability Gateway. `network: deny`
  returns `capability_denied`. Mounts are limited to `/workspace`, `/repo`,
  `/artifacts`, and `/tmp`. CPU/memory declarations are not OS isolation.
  Version is `0.40.0-dev`.

- Phase 39 SDK in-process transport: Capability Gateway encodes `{sdk, method,
  arguments}` for `sdk-development` / `obsion.development.echo`. pip/module/url and
  non-empty egress fail closed. Version is `0.39.0-dev`.

- Phase 38 MCP in-process transport: Capability Gateway encodes JSON-RPC `tools/call`
  for `mcp-development` / `obsion.echo`. Remote URLs, stdio spawn, and non-empty
  egress fail closed. Version is `0.38.0-dev`.

- Phase 37 IM loopback webhook: `obsion-im serve --listen 127.0.0.1[:port]` accepts
  documented callbacks on loopback. WeCom AES ciphertext and `--deliver http` fail
  closed. Version is `0.37.0-dev`.

- Phase 36 vendor IM outbound: `obsion-im` renders Feishu, DingTalk, and WeCom replies
  as documented local-outbox envelopes. `--deliver http` is rejected. Identity stays
  on `im_principal_bindings`. Version is `0.36.0-dev`.

- Phase 35 Experience Eval: Workbench **评测台** and `/api/v1/eval` wrap the existing
  evaluation engine. `fixtures.actual` is rejected. Compare uses two completed runs on
  the same dataset snapshot. Conversation still has one assistant. Version is
  `0.35.0-dev`.

- Phase 34 Experience Studio: Workbench **Studio 开发台** and `/api/v1/studio`
  validate/publish/promote Agent and Skill manifests. Unpublished versions do not
  bind new Turns. Conversation still has one assistant. Version is `0.34.0-dev`.

- Phase 33 Experience Desktop: `@obsion/desktop` (`obsion-desktop`) is a first-class
  App Server client with a loopback window shell. Electron is an optional window host
  and may only load `http://127.0.0.1`. Credentials stay in `desktop.secret` or
  `OBSION_TOKEN`, never in config JSON. Version is `0.33.0-dev`.

- Phase 32 vendor IM inbound: `obsion-im` translates documented Feishu, DingTalk, and
  WeCom callback envelopes onto the existing ingest contract. Vendor names are
  identity namespaces, not HTTP clients. Nicknames still cannot authorize. Outbound
  remains the local development outbox. Workbench administration manages IM bindings.
  Version is `0.32.0-dev`.

- Phase 31 IM principal mapping: `(channel, sender_id)` binds to `users.id`. Unmapped
  senders fail closed. Nicknames have zero authorization weight. Delegated ingest
  creates the Turn as the bound User. Version is `0.31.0-dev`.

- Phase 30 Experience IM adapter: `obsion-im` is a first-class App Server client for
  inbound development-channel messages. One conversation maps to one Thread. Feishu,
  DingTalk, and WeCom are not implemented and must not be faked. Version is
  `0.30.0-dev`.

- Phase 29 Experience IDE: `@obsion/ide-extension` is a first-class App Server client
  for Workspace/Thread/Turn/Run, Evidence, Claims, and approvals. It does not
  implement Harness. Settings cannot store credentials; Secret Storage or
  `OBSION_TOKEN` supplies the bearer. Version is `0.29.0-dev`.

- Phase 28 Reflect critic replan: after VERIFY, Reflect may `REPLAN` when Critic
  reports missing required Evidence and unused authorized read-only capabilities
  remain. Empty Evidence payloads do not count as coverage. Version is `0.28.0-dev`.

- Phase 27 Harness REFLECT: ordinary Runs persist `VERIFY → REFLECT → RESPOND`.
  Reflect records `reflect.respond` or `reflect.withhold` before publication.
  Missing-evidence replan moves all three trailing steps together. Version is
  `0.27.0-dev`.

- Phase 26 Experience CLI: `obsion-cli` is a first-class App Server client for
  Workspace/Thread/Turn/Run, Evidence, Claims, and approvals. It does not implement
  Harness. Python/TypeScript SDKs wrap the remaining App Server methods and REST
  `/api/v1/approvals`. Config files cannot store credentials. Version is `0.26.0-dev`.

- Phase 25 release hardening: evaluation gate (`evaluations/gates/v1-release.yaml`),
  secret scanning, CycloneDX SBOM from `uv.lock`, HTTP connector circuit breaker,
  Helm Ingress+Egress NetworkPolicy and optional API HPA, run/model/capability
  latency histograms, threat model, backup/restore, upgrade, and SLO documents.
  Staging deploy, CVE scanning, and human sign-off remain operator-owned; version
  stays `0.25.0-dev` rather than a signed `1.0.0`.

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

### Fixed

- Phase 85 verification-admission trigger repair (Alembic `b88f1c4d5e60`): the
  drill discovered that `obsion_validate_verification_assessment()` resolved
  its candidate id through one SQL CASE expression, so plpgsql validated the
  `NEW.assessment_id` reference at plan time and every insert into
  `verification_assessments` failed at COMMIT on real PostgreSQL. The function
  body now resolves the id in branch statements; verification rules are
  byte-identical and a downgrade/upgrade round trip was verified.
