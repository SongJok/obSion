# PHASE-75-REPORT — Release-note consolidation

## What was implemented

Phase 75 consolidates the operator contract for Phases 68-74.

- Human release notes document the Feishu/DingTalk/WeCom Experience and Knowledge
  support matrix, required secret names, pinned origins, rollout, smoke tests,
  rollback, and known limitations.
- A versioned `ReleaseNotes` YAML contract makes phase continuity, migration posture,
  documents, origins, environment names, and operational procedures verifiable.
- `obsion validate-release-notes` runs from the Makefile and CI.
- Release contract tests cross-check vendor source constants, connector examples,
  `.env.example`, changelog order, operator guidance, and fail-closed validation.
- Administrator, runbook, deployment, upgrade, developer, connector, roadmap, README,
  and changelog documentation no longer contain pre-Phase-68 support claims.
- SBOM generation now uses the authoritative project-status version rather than a
  hard-coded Phase 25 version.
- ADR 0054 records release notes as operational contracts rather than marketing copy.

## Architecture decisions

The release manifest is validation input only. It cannot enable a connector, resolve
a secret, authorize a Principal, or execute a Capability. IM remains an Experience
client of one App Server; vendor Knowledge remains behind Capability Gateway,
Policy, explicit ACL, bounded budgets, Evidence, and Audit.

## Migration

Phases 68-75 add no Alembic revision. The release contract declares `database: none`
and an empty revision list. Alembic drift validation remains mandatory.

## Validation

- `make check` — passed from a clean command invocation: Ruff format/lint, strict
  mypy (200 source files), contract/evaluation/release-note/secret gates, frontend
  lint/typecheck, 777 Python tests passed with 22 documented opt-in/live skips, 50
  Desktop/IDE/TypeScript SDK tests passed, and Alembic reported no drift.
- PostgreSQL opt-in integration suite — 15 passed, 2 historical destructive
  migration cases skipped behind their dedicated CI switches.
- Vendor Phase 68-74 + IM adapter regression — 118 passed, 1 live Feishu HTTP test
  skipped as operator-owned.
- Phase 75 release contract suite — 9 passed, including malformed manifest failure
  cases and source/connector/environment cross-checks.
- `uv run obsion validate-release-notes` — version `0.75.0-dev`, contiguous Phases
  68-74, three vendors, no database migration, and all referenced documents valid.
- `uv run obsion scan-secrets` — 0 findings.
- `alembic upgrade head` applied the existing Phase 62 IM-delivery revision to the
  local development database; `alembic check` then reported no new operations. Phase
  75 itself adds no revision.

## Remaining risks

- Live tenant application scopes, public DNS/TLS, callback registration, and
  vendor-side quotas remain operator-owned.
- The current Helm chart does not deploy the separately managed `obsion-im` public
  listener or provision vendor applications.
- The host provides JDK 17 while the existing REST SDK requires JDK 21. Its local
  Maven run could not execute cached Java 21 test classes; CI's dedicated JDK 21 job
  remains the authoritative Java SDK gate. Phase 75 changes no Java source.
- Staging deploy, timed backup/restore, registry-side HIGH CVE policy, and human
  security/data-owner sign-off remain operator-owned from Phase 25.
