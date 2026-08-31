# Phase 25 release, evaluation, and hardening review

## Review question

Can the completed control plane be treated as an engineering-ready V1 candidate:
evaluation contracts and gates exist, dangerous SQL and writes stay fail-closed,
connectors are SSRF-bounded with a circuit breaker, Helm defaults deny extra
ingress, secrets are scanned, and an SBOM can be generated — without claiming that
staging, UAT, or a human security review already happened?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- Golden Datasets cover Knowledge, Data, Engineering, Incident, Support, Operation,
  and Analytics routes. `evaluations/gates/v1-release.yaml` requires those routes,
  ROUTING/SQL_POLICY/RUN_OUTPUT evaluators, and 1.0 pass / 0 error / 0 regression.
- Prompt injection cannot open write capabilities or unauthorized SQL. Support and
  Viewer roles lack `action.execute`. HTTP connectors reject egress outside the
  allowlist. Repeated transport failures open a fail-closed circuit using
  `capabilities_unavailable`.
- Helm NetworkPolicy is Ingress+Egress, ingress is namespace-scoped, and API HPA is
  optional. API pods drain with `terminationGracePeriodSeconds` and a preStop hook.
  Optional `encryption.existingSecret` injects `OBSION_SECRET_ENCRYPTION_KEY`.
  Secret scanning skips tests and local `.env`. CycloneDX SBOM is derived
  from `uv.lock`.
- Run, model, and capability latency histograms exist, plus TTFT, replan counts, and
  model cost. Knowledge search results are clamped. Policy evaluation is timed and
  approval decisions are counted. Backup, restore, upgrade, SLO, deployment,
  administrator, developer, Agent/Skill, incident, and threat-model
  documents exist. Staging deploy, HIGH CVE policy, live OIDC, and human
  sign-off remain operator-owned.

## Automated acceptance map

- `test_phase25_release_hardening.py` covers injection, role bounds, SQL UNION/stacked
  statements, SSRF, circuit breaking, secret scan, eval gates, Helm policy, health,
  concurrent greeting Runs, concurrent SSE streams, clamped knowledge retrieval,
  Agent sandbox network bounds, and offline ROUTING/SQL_POLICY evaluation.
- CI runs `validate-eval-gates`, `evaluate-datasets`, `scan-secrets`, `sbom`, and
  Trivy CRITICAL scans in addition to existing contract, pytest, frontend,
  migration, and image-build jobs.

## Human review checklist

- Execute a staging deploy from clean infrastructure and a backup/restore drill.
- Apply the organization's HIGH CVE policy against the SBOM and promoted image digests.
- Confirm live OIDC, secret manager, and production replicas before calling the
  release `1.0.0`.
