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
Committed Golden Datasets are validated in CI together with `evaluations/gates/v1-release.yaml`;
candidate releases bind their real terminal Runs, compare against an exact-snapshot
baseline, and must pass configured pass-rate, regression-rate and named-score gates.
Staging deploy, container CVE scanning, and human sign-off remain operator-owned and
are not implied by `0.64.0-dev`. Phase 26 adds `obsion-cli` as an Experience client of
the App Server; Phase 27 persists Harness REFLECT between VERIFY and RESPOND; Phase 28
lets Reflect replan before publication; Phase 29 adds the VS Code Experience client;
Phase 30 adds the development IM adapter; Phase 31 binds IM sender ids to provisioned
Principals; Phase 32 translates documented vendor IM envelopes without calling vendor
HTTP; Phase 33 adds the Desktop Experience client; Phase 34 adds Studio as a governed
Agent/Skill workbench; Phase 35 adds Eval as a governed Golden Dataset console;
Phase 36 renders vendor IM outbound as local-outbox envelopes without HTTP POST;
Phase 37 hosts a 127.0.0.1 webhook for those envelopes; Phase 38 installs MCP as an
in-process Capability Gateway transport without spawning remote MCP processes;
Phase 39 installs SDK as an in-process Gateway transport without pip/importlib
installs. Phase 40 pins Agent sandbox on the Run and enforces `network: deny` at
the Capability Gateway without claiming container isolation. Phase 41 installs gRPC
as an in-process Gateway transport without remote channels or grpcio. Phase 42
installs WORKFLOW as an in-process Gateway transport without Temporal/Airflow or a
second orchestrator. Phase 43 installs AGENT as an in-process Gateway transport
without nested Harness loops or an Agent picker. Phase 44 binds WORKFLOW
`workflow_id` to `AutomationService.trigger_workflow` with a depth-1 recursion
budget. Phase 45 adds a JDK 21 Java REST SDK and Connector/Capability-bind wrappers
on Python and TypeScript; it is not a second control plane. Phase 46 adds the Python
Connector SPI (`health`/`discover`/`execute`) as an in-process authoring contract
behind the Gateway; it is not a package installer or Java SPI. Phase 47 adds static
plugin scan, HMAC signature, registry promote, and L3+ approval for those adapters;
it is not a marketplace or binary scanner. Phase 48 adds Studio compare and Agent/Skill
rollback of immutable versions; Prompt snapshots can be compared but not rewritten;
runtime traffic is not split. Phase 49 pins those Prompt snapshots on each Harness
Run and Eval start so Prompt Change is reproducible. Phase 50 renders those pinned
templates with schema-bound governed values only. Phase 51 makes Context Builder
record Keep / Compress / Summarize / Drop and pins that extractive ledger on the
Run. Phase 52 compacts older conversation through an extractive interface, not a
nested model call. Phase 53 pins Workspace Context on the Run and keeps workspace
description out of SYSTEM trust. Phase 54 isolates Capability tool results as an
untrusted `tool-result` segment. Phase 55 projects goal.txt core rates from
PostgreSQL and refuses to treat OTel histograms as a p95 SLA. Phase 56 adds
path-versioned Workspace Files on the Artifact store. Phase 57 publishes
workspace REPORT artifacts from evidenced Runs and refuses to invent a
dashboard. Phase 58 publishes DASHBOARD artifacts that only reference existing
CHART/TABLE/SQL rows and refuses to invent Vega series. Phase 59 lists
published workspace SQL artifacts and refuses to invent warehouse rows. Phase 60
lists persisted workspace Evidence and refuses to invent citations. Phase 61
lists persisted Run Events as a workspace timeline and refuses to invent
Harness steps. Phase 62 delivers Feishu replies through an explicit
`feishu-http` transport after Policy authorization and refuses generic
`--deliver http`. Phase 63 verifies official Feishu `X-Lark-Signature`
headers and decrypts documented AES-256-CBC events on the loopback
listener. Phase 64 ingests Feishu cloud documents through the
`feishu-docs` connector into the existing Knowledge pipeline and refuses
to invent ACL or treat IM Experience as a document source. Phase 65
walks a Feishu wiki space and syncs only `docx` nodes through that same
pipeline. Phase 66 ingests Confluence Cloud pages through a site-pinned
`confluence` connector and refuses off-origin pagination or invented ACL.
Phase 67 adds explicit public Feishu webhook TLS after Host allowlist checks
and refuses unsigned public binds. Phase 68 delivers DingTalk and WeCom
replies through explicit `dingtalk-http` / `wecom-http` transports after
Policy authorization and still refuses generic `--deliver http`. Phase 69
decrypts WeCom `Encrypt` with EncodingAESKey after optional Token signature
checks. Phase 70 extends `--public` TLS ingress to DingTalk and WeCom with
channel-specific security. Phase 71 ingests DingTalk cloud documents through
the `dingtalk-docs` connector into the existing Knowledge pipeline and refuses
to invent ACL or treat IM Experience as a document source. Phase 72 ingests
WeCom wedoc documents through the `wecom-docs` connector into that same pipeline
and refuses invented organization ACL. Phase 73 hardens shared Vendor Knowledge
sync budgets, provenance metadata, and Gateway-aligned REST rate limits. Phase 74
surfaces those provenance fields in the Workbench Knowledge view and Runtime
Inspector without inventing missing values. Phase 75 consolidates Phases 68-74 into
human-readable and machine-validated operator release notes, corrects stale support
copy, and derives SBOM project version from the authoritative project status. None of
these change the
operator-owned release checklist.

Phase 76 adds explicit non-sending Feishu live validation and correct Feishu HTTP 400
business-error classification. Phase 77 unifies all vendor REST ingest/sync writes on
a no-Run entry in the same Capability Gateway without weakening the generic read-only
Agent boundary or fabricating Harness state. Phase 78 closes the remaining vendor
source-management bypass: spaces/workspaces/nodes/pages browse through versioned L1,
side-effect-free `knowledge.source.containers` / `knowledge.source.items` Capabilities
with Policy, grants, schemas, shared rate keys, credentials, telemetry, and Audit.
Phase 79 closes the no-Run write retry gap with a principal-scoped durable claim,
terminal replay, input-conflict detection, UNKNOWN reconciliation state, PostgreSQL
immutability, and content-free admin/SDK/Workbench inspection. Phase 80 freezes the
first repository-wide Alpha.1 release contract: one machine-validated manifest,
human release notes, reproducible verification matrix, migration/SBOM evidence, and
explicit operator-owned boundaries. It does not open production writes or publish an
external tag by itself. Phase 81 adds bounded read-only Feishu bot chat discovery
and an opt-in, single-message live send probe through the production `feishu-http`
channel contract; the probe requires `OBSION_FEISHU_SEND_LIVE=1`, environment
credentials, and an explicit `OBSION_FEISHU_LIVE_CHAT_ID`, never auto-discovers a
target, and fabricates no Harness state. The Alpha.1 manifest is frozen as a static
historical contract validated at its candidate commit. Phase 82 turns the Alpha.1
contract into installable evidence: one stdlib-only operator tool builds all four
Python distributions, the TypeScript SDK tarball, the Java SDK JAR (compiled inside
the pinned JDK 21 container), and both container images from a single revision into
the gitignored `dist/release/<version>/` tree, records SHA-256 hashes and image
identifiers in `artifact-manifest.json`, and validates every artifact in clean
temporary environments (fresh venv, temporary npm prefix, JAR listing, local image
smokes). Nothing is published externally; CVE scanning stays in CI.
Phase 83 makes that evidence release-candidate grade: builds refuse dirty source by
default, CI reproduces all artifact hashes and clean-room checks after the ordinary
quality/migration/Java/Helm gates, uploads a bounded candidate bundle, and validates an
exact mapping from all V1 requirements rows to shipped artifact identities and real
test evidence. Six staging, DR, registry/signature, live infrastructure, human
approval, and publication gates remain explicitly operator-owned; repository readiness
cannot be mistaken for production-promotion authority.
Phase 84 turns operator-run Feishu live validation into durable candidate evidence:
a declared `LiveEvidenceLadder` binds six probes to the existing opt-in pytest
nodes, `obsion record-live-evidence` records redacted, checksummed ledgers under
`docs/release/evidence/alpha1/` with fail-closed classification (a skip is never a
pass), and the candidate gate validates union coverage offline without vendor
traffic. Two real ledgers were recorded at revision `467fe95`: tenant
authentication, chat discovery, fail-closed document/wiki/browse denial on
ungranted scopes, and one live `feishu-http` delivery into the Phase 81 ephemeral
validation chat. Recorded live evidence never feeds `promotion_eligible`;
DingTalk, WeCom, public TLS ingress, and permitted Feishu document content remain
operator-owned.

Phase 85 turns backup/restore from runbook prose into recorded candidate
evidence: a declared `DrillEvidenceLadder` migrates a throwaway pinned
PostgreSQL 17 container with Alembic, seeds a governed Harness scenario through
the real REST API, restores a custom-format `pg_dump` into a fresh target, and
verifies schema-version, row-count, referential-integrity, and audit-identity
parity in a redacted, checksummed ledger the candidate gate validates offline.
The drill immediately earned its keep by surfacing a latent
verification-admission trigger defect that failed every
`verification_assessments` insert on real PostgreSQL; Alembic revision
`b88f1c4d5e60` repairs the function body and the green ledger now proves a real
governed Run completes against migrated PostgreSQL. Recorded drill evidence
never feeds `promotion_eligible`; the staging-scoped restore, object-storage
restore, and the remaining five operator gates stay operator-owned.

Phase 86 completes the repository-level recovery story by recording the
object-storage half: a declared `ArtifactDrillEvidenceLadder` migrates a
throwaway pinned PostgreSQL 17 container, seeds knowledge and file artifacts
through the real REST API into a throwaway pinned MinIO container via the
production `MinioObjectStore` write path, snapshots the bucket into a
canonical per-object SHA-256 manifest, restores from snapshot bytes into a
fresh bucket, and verifies key-set, content-checksum, metadata, and — the
binding invariant — database-reference consistency between
`artifacts.storage_key`/`document_versions.content_ref` rows and restored
objects. A new `OBSION_OBJECT_STORE_BACKEND` setting selects the artifact
backend explicitly without changing existing environments, and the candidate
contract's `drillEvidence` section is now a `ladders` list binding both drill
families (`drill_evidence_ledgers: 2`, `drill_evidence_checks: 16`). Recorded
drill evidence never feeds `promotion_eligible`; the staging-scoped timed
restore with measured RPO/RTO and the remaining five operator gates stay
operator-owned.

Phase 87 makes restore-path health continuously visible: a scheduled
`drill.yml` workflow runs both ladders nightly and on manual dispatch on
docker-capable hosted runners, writing fresh ledgers to the runner temp
directory and uploading them as short-retention CI artifacts, so a broken
migration, seed, dump, restore, or parity check turns red within one schedule
tick instead of waiting for the next operator recording. The workflow is
deliberately not a merge gate (external registry health must never block a
pull request), carries no credentials or write permissions, and never
overwrites the committed operator-recorded ledgers. ADR 0066 records the
decision. CI-generated evidence never feeds `promotion_eligible`; all six
operator gates remain PENDING.

Phase 88 hardens the experience layer without expanding the frozen Alpha.1
surface: every Workbench request is bounded by an explicit timeout with
normalized timeout/network/parse errors, the single-page app gains
route-level error/loading/not-found boundaries, the governance console
degrades per domain instead of failing closed on any one of its 22
endpoints, Eval results are generation-guarded against stale overwrites,
Data and Code views distinguish loading/empty/no-match states, the Runtime
inspector resets detail selection on Run change and shows the
live/polling/interrupted sync state instead of hiding the REST fallback,
and governed-action preflight declarations plus Desktop/IDE approval
reasons are operator-entered or cancelled — no canned rationale can enter
an approval or audit record. ADR 0067 records the decisions. Client code
only; the candidate contract, recorded evidence, and all six PENDING
operator gates are untouched.

Phase 89 delivers goal.txt section 57's Evidence Panel on persisted rows:
Workbench Evidence details now dispatch on the normalized content envelope
— observability event streams for METRIC/LOG/TRACE/deployment.list,
engineering change items for GIT/CONFIG/deployment.commit/k8s.status with
git.diff patch views and config.diff before/after pairs, HTML tables and
explain plans for DATA, Code Graph symbol locations for CODE, citation
provenance for knowledge hits, and readable document text for attachments —
with a preserved raw JSON fallback for any payload no classifier
recognizes and a full metadata ledger (observed/ingested time, confidence,
classification, permissions, fingerprint, step, Run, lineage) rendered
only from persisted fields. The Web Evidence type now matches the REST
projection exactly. ADR 0068 records the decisions. Rendering only; the
candidate contract, recorded evidence, and all six PENDING operator gates
are untouched.

Phase 90 turns the Runtime timeline into a per-stage investigation
narrative projected from persisted keys only: each step shows its
persisted duration, the Evidence rows carrying its step_id as bounded
typed chips that open the Phase 89 typed detail, and the claims that
evidence supports as badges jumping to the Claims tab; evidence without
a step stays visible in an explicit unattributed section. No link is
inferred and no prose is generated. ADR 0069 records the decisions.
Rendering only; the candidate contract, recorded evidence, and all six
PENDING operator gates are untouched.

Phase 91 executes the ADR 0067-deferred decision and gives apps/web an
executable test stack: exact-pinned dev-only vitest, jsdom, and Testing
Library with 34 behavior-level tests covering the typed Evidence
classifier and renderers, citation helpers, and API error normalization,
wired into the existing root npm test fan-out so make test and CI run it
unchanged. The Python static contract suites remain in force. ADR 0070
records the decisions. Dev-only; the candidate contract, recorded
evidence, and all six PENDING operator gates are untouched.

Phase 92 closes the gap audit's top P1 item and completes the
collaboration assignment and source-Run provenance surface:
WorkspaceMemberView carries readable display_name and email, task
create/edit offers a member selector with explicit-null clearing, task
and decision creation attach a bounded newest-first source Run,
provenance renders on every record and opens the full Runtime inspection
bundle through the existing loadInspection path, and invalid-assignee
or cross-workspace-Run rejections map to actionable messages. The
Runtime inspector route labels now cover ANALYTICS, OPERATION, and
SUPPORT. ADR 0071 records the decisions. The candidate contract,
recorded evidence, and all six PENDING operator gates are untouched.
