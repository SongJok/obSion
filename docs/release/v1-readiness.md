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

## Operator-owned (not claimed by this repository run)

- [ ] Staging deploy from clean infrastructure
- [ ] Staging-scoped backup/restore drill with timed RPO/RTO (repository-local drill evidence is recorded above)
- [ ] Registry-side HIGH CVE policy and signed production image promotion
- [ ] Live OIDC, secret manager, and production replicas
- [ ] Human security and data-owner sign-off
- [ ] Maintainer-authorized signed tag/package/image publication

Do not set `docs/project-status.yaml` to a signed `1.0.0` until the operator-owned
items are evidenced outside this control plane. `obsion validate-release-candidate`
reports repository readiness separately and `--require-promotion-eligible` fails while
any item above remains represented by a `PENDING` operator gate.
