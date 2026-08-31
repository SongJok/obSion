# PHASE-80-REPORT — Alpha.1 repository release contract

## What was implemented

- Added the repository-wide `0.80.0-alpha.1` human and machine release contracts.
- Extended release validation to bind project status, continuous Phase reports,
  architecture reviews, the exact linear Alembic chain, SBOM version, publication
  posture, and Experience-or-Knowledge vendor contracts.
- Restored missing Phase 1–14 and 16–20 reports as explicitly retrospective records
  grounded in existing implementation, architecture packets, and current tests.
- Added Confluence Knowledge-only release metadata without inventing an IM surface.
- Switched the CLI/CI default release manifest while preserving explicit validation of
  `0.75.0-dev`.

## Architecture decisions

ADR 0059 defines Alpha.1 as a repository-evidenced candidate. It is not an external
publication, signed tag, production release, human approval, or authority expansion.
Runtime architecture and all V1 production-deny boundaries remain unchanged.

## Migration

No new database or Event migration is added. The manifest declares the complete
30-revision Alembic chain and the validator statically proves one base-to-head lineage.

## Validation

- `make check` passed: Ruff format/lint over 656 files, strict mypy over 202 source
  files, 314 error codes, 93 Event versions, 38 evaluation cases and the V1 eval gate,
  Alpha.1 release validation, dataset execution, zero secret findings, frontend
  lint/typecheck, 811 Python tests passed with 25 explicit opt-in/live skips, 50
  Desktop/IDE/TypeScript SDK tests passed, and Alembic reported no drift.
- Phase 75 legacy plus Phase 80 Alpha release contracts — 16 passed.
- Alpha.1 manifest verified 80 Phase reports, 80 architecture reviews, all 30 Alembic
  revisions through `a79c4d2e8f10`, 24 referenced documents, four vendor contracts,
  false external publication/signature claims, and the matching CycloneDX version.
- JDK 21 Java SDK regression — 6 passed in an isolated container.
- PostgreSQL Phase 79 concurrency/immutability and isolated migration round-trip —
  2 passed; no Phase 80 schema change was introduced.
- Real non-writing Feishu Capability browse — 1 passed with 828 tests deselected;
  credentials were process-local and never printed or persisted.

## Remaining risks

- External publication/tagging, clean staging, UAT, timed DR, live OIDC/secret manager,
  permitted tenant data, and human security/data-owner sign-off remain operator-owned.
- Default RUN_OUTPUT datasets still require real terminal Run bindings for release
  scoring; repository validation does not fabricate those observations.
