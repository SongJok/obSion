# V1 readiness checklist

This is an engineering checklist, not a human production approval.

## Automated (must pass in CI)

- [x] Unit, contract, and default e2e tests
- [x] Event and error catalogs
- [x] Registry and Golden Dataset validation
- [x] Evaluation gate file (`evaluations/gates/v1-release.yaml`)
- [x] Secret scan of non-test sources
- [x] Helm lint/template
- [x] Control-plane and web image builds
- [x] Dangerous SQL blocked by AST policy
- [x] Production write capabilities fail closed
- [x] Support/Viewer cannot execute L3 actions
- [x] HTTP connector SSRF allowlist
- [x] Connector circuit breaker fail-closed
- [x] Knowledge retrieval and SQL result limits are bounded
- [x] Run/model/capability/SQL/retrieval latency, TTFT, replan, step count, and model cost instruments
- [x] Concurrent SSE Run streams close after terminal events
- [x] Large knowledge retrieval is clamped
- [x] CI Trivy filesystem and image scans fail on unfixed CRITICAL findings
- [x] ROUTING and SQL_POLICY Golden Dataset cases execute against production code
- [x] Eight concurrent greeting Runs complete within the documented SLO
- [x] Helm termination drain and optional encryption secret interface
- [x] Operator, administrator, developer, connector, Agent/Skill, and incident docs
- [x] Continuous Phase 1-83 reports and matching architecture review documents
- [x] Repository-wide Alpha.1 manifest, exact Alembic ancestry, and matching CycloneDX version
- [x] Clean-source CI artifact manifest, clean-room installation, exact requirements mapping, and retained candidate report
- [x] Recorded, redacted, checksummed Feishu live-tenant evidence ledgers validated offline by the candidate gate
- [x] Recorded, redacted, checksummed backup/restore drill ledger (PostgreSQL 17 dump/restore parity) validated offline by the candidate gate
- [x] Recorded, redacted, checksummed artifact-store drill ledger (bucket snapshot/restore parity plus database-reference consistency) validated offline by the candidate gate
- [x] Scheduled CI drill signal running both ladders nightly (fail-closed, credential-free, never overwriting recorded ledgers)
- [x] Workbench and experience-client reliability hardening: bounded normalized requests, route-level error boundaries, per-domain admin degradation, generation-guarded async state, visible stream fallback, and operator-entered governance declarations
- [x] Typed Evidence views rendering persisted Evidence envelopes (observability events, engineering change items with diff specialization, data tables, explain plans, code symbols, knowledge citations) with a preserved generic fallback and full metadata ledger
- [x] Per-stage investigation narrative correlating steps, evidence, and claims through persisted keys (step_id, evidence_ids, timestamps) with bounded display and an explicit unattributed-evidence section
- [x] Executable JavaScript test stack for apps/web (vitest + Testing Library, dev-only exact-pinned) covering the typed Evidence core, citation helpers, and API normalization, wired into the root npm test fan-out
- [x] Collaboration assignment and source-Run provenance: readable member identity on the member view, member selector with explicit-null clearing, bounded source-Run selection on task/decision creation, provenance display with a Runtime inspector link, and actionable validation-error mapping
- [x] Native Anthropic and Gemini model adapters behind the existing provider protocol with lossless tool_choice mapping, per-vendor json_mode, fail-closed parsing, and registry-driven admin validation
- [x] Automation Web authoring depth: immutable version management with inspect, derive, publish and rollback, a spec viewer, validated trigger payloads with idempotency, schedule authoring with fixed-version pinning, guarded retire, and step output refs linked into the Runtime inspector
- [x] Schema-driven chart renderer honoring the emitted Vega-Lite subset: temporal line charts with grid ticks and tooltips, big-number text marks, capped bars, fail-closed parsing, and a producer contract pinned against drift
- [x] Post-conclusion context actions: verified claims become workspace tasks or decision records from the Runtime inspector with editable prefilled payloads, source-Run provenance, and run-pinned workspace targeting
- [x] Broader Workbench interaction tests: composer, claim-action, collaboration, and Automation lifecycle flows driven through mounted components with a production-typed mocked API boundary, plus a fixed defect where collaboration error guidance was erased by its own follow-up refresh
- [x] Runtime inspector accessibility and depth: six tabs implement tablist/tab/tabpanel semantics with roving ArrowLeft/ArrowRight/Home/End focus, and interactions traverse context, Evidence, governed Memory, Claim-to-Evidence navigation, and Artifact details
- [x] Governance-console interactions: independent 22-domain degradation, stable-sender IM binding/revocation, and Connector SDK health/discovery/scan/promotion are mounted and driven through the typed Admin API boundary; discovery is verified not to auto-bind a Capability
- [x] Governed Action interactions: development/staging-only draft creation, idempotency, operator-authored preflight, independent approval reasoning, bounded rollback reasoning, and API-mediated cancellation are mounted and driven; the UI exposes no production option
- [x] Governed Action modal accessibility: draft creation, approval, and preflight/rollback reason surfaces expose named dialog roles and modal semantics
- [x] Studio interactions and accessibility: Agent/Skill/Workflow kinds use roving tab semantics; Workflow stays validation-only; immutable publish/promote/rollback and no-traffic-split comparison are driven; selection changes clear stale baselines
- [x] Eval interactions and scoping: initial cases load once; dataset changes clear candidate/baseline/result/compare state; self-comparison is excluded; case and run-binding JSON are object-validated; pinned Evaluation Runs and same-dataset comparison are driven
- [x] Knowledge interactions and Evidence safety: authorized queries are normalized, prior Evidence clears before transport, failed searches cannot retain stale results, provenance is rendered, and ACL-bearing upload/vendor-specific Gateway ingestion/Feishu sync are driven without reading credentials
- [x] Data interactions and lineage safety: verified metrics filter by governed fields, definitions and read-only lineage render in accessible tabs, failures stay explicit, and generation guards prevent late responses from crossing metric or modal boundaries
- [x] Code interactions and result safety: authorized repository count and normalized symbol search are driven; authorized-empty and transport-failure states remain distinct; stale or slower cross-query symbols cannot overwrite the current projection
- [x] Workspace Files and Artifacts interactions: immutable history, filename-derived paths, classification/lineage metadata, accessible uploads, governed downloads, filters, previews, and Artifact-ID-preserving refresh are mounted and driven
- [x] Workspace fact projections: persisted Reports, unique valid Dashboard panel references with error recovery, validated SQL text without execution, immutable Evidence envelopes, and Event Store Timeline payloads are mounted and driven by persisted IDs
- [x] Offline contract distribution: Hatchling and its build closure are locked dev dependencies; a real wheel is built with no isolation and offline mode, then checked for exact frozen Event/Error resources; the SBOM reflects the lock

## Operator-owned (not claimed by this repository run)

- [ ] Staging deploy from clean infrastructure
- [ ] Staging-scoped backup/restore drill with timed RPO/RTO (repository-local PostgreSQL and artifact-store drill evidence is recorded above)
- [ ] Registry-side HIGH CVE policy and signed production image promotion
- [ ] Live OIDC, secret manager, and production replicas
- [ ] Human security and data-owner sign-off
- [ ] Maintainer-authorized signed tag/package/image publication

Do not set `docs/project-status.yaml` to a signed `1.0.0` until the operator-owned
items are evidenced outside this control plane. `obsion validate-release-candidate`
reports repository readiness separately and `--require-promotion-eligible` fails while
any item above remains represented by a `PENDING` operator gate.
