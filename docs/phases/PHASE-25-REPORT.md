# PHASE-25-REPORT — Enterprise hardening, evaluation, and release candidate

## What was implemented

Phase 25 is the repository's release-hardening gate. It does not add a new
intelligence path. It proves that the existing Harness, Gateway, Policy, Evidence,
and specialist Agents can be evaluated, scanned, and deployed with fail-closed
defaults.

- Evaluation: `evaluations/gates/v1-release.yaml` requires ROUTING, SQL_POLICY, and
  RUN_OUTPUT, plus KNOWLEDGE/DATA/ENGINEERING/INCIDENT/SUPPORT/OPERATION/ANALYTICS.
  Golden Datasets add UNION and stacked-statement SQL denials, plus Engineering and
  Support RUN_OUTPUT contracts. `obsion validate-eval-gates` fails CI when a required
  evaluator or route is missing. `obsion evaluate-datasets` executes ROUTING and
  SQL_POLICY cases against production Understanding and SQL AST code.
- Security: prompt-injection plans cannot select ticket/cluster writes; Support and
  Viewer cannot `action.execute`; HTTP connectors still deny SSRF; a process-local
  circuit breaker opens after repeated transport failures and raises
  `capabilities_unavailable` without a new error code. `obsion scan-secrets` fails on
  literal DSN/key material outside tests. CycloneDX SBOM is generated from `uv.lock`.
- Reliability and deploy: Helm NetworkPolicy is default-deny Ingress+Egress with
  scoped ingress and HTTPS/data-store egress; optional API HPA is templated. API pods
  drain with a termination grace period. Optional Helm `encryption.existingSecret`
  injects the envelope key. Health live/ready remain unauthenticated. Concurrent
  greeting load meets the documented local SLO. OpenTelemetry now records run,
  capability, model, SQL, retrieval, policy, and workflow duration plus model
  token counts and approval decisions. Shipped Agent manifests require
  `sandbox.network: gateway-only`; missing sandbox defaults to that bound.
- Documentation: threat model, backup/restore, upgrade, SLO, deployment,
  administrator, developer, Agent/Skill, incident, and a v1 readiness checklist that
  separates automated CI evidence from operator-owned staging work.
- Retrieval and SQL limits: knowledge search clamps `limit` to
  `knowledge_max_results` (default 50) even when a capability payload asks for more.
  SQL AST policy still rewrites oversized LIMIT. Concurrent greeting Runs expose
  ordered Event streams including `run.completed`.

## Architecture decisions

Circuit breaking sits after Policy, grants, TLS, and egress checks so an open circuit
cannot bypass authorization. The breaker reuses `capabilities_unavailable` (503) to
avoid expanding the frozen error catalog. Evaluation gates validate dataset contracts;
they do not invent RUN_OUTPUT scores without a bound terminal Run. Production write
capabilities, L4-L5 actions, and live ITSM/warehouse adapters remain fail-closed.

## Validation

- `uv run pytest` — 421 passed, 18 opt-in PostgreSQL tests skipped, including
  `test_phase25_release_hardening.py`.
- `uv run obsion evaluate-datasets` — 11 ROUTING/SQL_POLICY cases executed and
  passed; 27 RUN_OUTPUT cases remain skipped until terminal Runs are bound.
- Registry/evaluation validation: 38 Golden Dataset cases (7 ROUTING, 4 SQL_POLICY,
  27 RUN_OUTPUT) across Knowledge, Data, Engineering, Incident, Support, Operation,
  and Analytics routes.
- `uv run obsion validate-eval-gates`, `scan-secrets` (0 findings), and `sbom`.
- Event catalog remains 93 versions; error catalog remains 270 codes / 268 active
  origins. Circuit open reuses `capabilities_unavailable`.
- Helm template continues to lint; CI builds control-plane and web images without
  pushing.

## Remaining risks (not claimed complete)

- Staging deployment from clean infrastructure was not executed in this environment.
- Backup/restore was documented, not timed as a drill against a live cluster.
- CI Trivy fails the build on unfixed CRITICAL findings in the source tree and CI
  images. HIGH findings, private-registry policy, and live OIDC remain operator-owned.
- Therefore `docs/project-status.yaml` records `0.25.0-dev`, not a signed `1.0.0`.
