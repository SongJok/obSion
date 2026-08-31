# PHASE-83-REPORT — Alpha.1 release-candidate hardening

## What was implemented

- Hardened `scripts/release_artifacts.py` so release builds require a clean git tree,
  explicitly mark development-only dirty builds, clean stale Maven outputs, record
  container SHA-256 identities, and optionally require clean-source validation.
- Added the `ReleaseCandidateGate` validator and CLI. It binds the current project
  version, exact artifact identities, clean-room steps, the complete requirements
  matrix, repository evidence, and operator gates into a fail-closed candidate result.
- Added `docs/release/alpha1-candidate-gates.yaml`: twelve expected artifacts, eleven
  mandatory clean-room steps, four shipped surfaces, all 37 unique V1 requirements,
  and six explicit external-promotion gates.
- Reworked the CI artifact job to depend on quality, migration, Java SDK, and Helm;
  build and validate the release bundle; generate the candidate report; scan the exact
  images; and retain `dist/release/` for fourteen days without publishing it.
- Added Make targets for static and full candidate validation, wired static validation
  into `make lint`, and published the 0.83.0-dev machine/human release contract.

## Architecture decisions

ADR 0062 separates repository readiness from promotion authority. Clean, complete,
traceable artifacts can pass CI while `promotion_eligible` remains false. A pending
operator gate cannot include evidence or approval, and full promotion mode fails until
all real external evidence is present.

No runtime path changed: the one Python control plane, one App Server, durable Harness
hierarchy, Capability Gateway, Policy, Evidence, and credential boundaries remain
unchanged.

## Migration

No database or Event revision is added. Alembic drift validation remains part of the
phase gate.

## Validation

- `test_phase83_alpha1_release_candidate.py` (11 passed) covers the real contract, exact
  requirement/artifact coverage, clean-source/hash/skip failures, pending promotion
  denial, CLI defaults, CI wiring, project status, and release manifest.
- Historical Phase 80–82 release tests continue to validate their frozen contracts.
- `make check` covers Ruff formatting/lint, strict mypy, contract/Event/evaluation and
  release validation, secret scanning, all Python and frontend tests, and Alembic
  drift: 853 Python tests passed, 27 explicitly opt-in PostgreSQL/live-tenant tests
  were skipped, all Desktop/IDE/TypeScript SDK tests passed, and no schema drift or
  secret finding was reported.
- `make test-java` runs `clean test` inside the same pinned JDK 21 container as
  artifact packaging: all 6 Java SDK tests passed, so host JDKs and stale target
  classes cannot weaken validation.
- A full build was also executed from an isolated clean git snapshot containing the
  exact candidate source. The first validation exposed host `pip` TLS dependence and
  unlocked dependency resolution; the root fix switched the clean room to a
  hash-verified `uv.lock` export, wheel installation with `--no-deps`, and `uv pip
  check`. The rebuilt snapshot produced all 12 artifacts and passed all 21 validation
  steps: 10 file hashes, locked Python dependencies and four wheels, dependency and
  import/CLI checks, Node import, Java class loading, control-plane image import, and
  Workbench HTTP 200.
- Full `validate-release-candidate` then passed with 37 requirements, 4 coverage
  surfaces, 12 exact artifacts, and 6 explicitly pending operator gates. The actual
  `--require-promotion-eligible` run failed as designed on those gates. The temporary
  validation revision is test evidence, not an external tag or authoritative release
  commit; clean CI must reproduce the bundle after commit.

## Remaining operator gates

- Clean staging/UAT, timed PostgreSQL/object-store restore, registry HIGH/CRITICAL CVE
  policy and signatures, live OIDC/secret manager/read replicas, security/data-owner
  approval, and maintainer-authorized publication remain `PENDING`.
- CI file artifacts expire after fourteen days. Images are smoke-tested and scanned on
  the runner but are not pushed.
- Phase 84 cannot start promotion work by inference; it requires real evidence and
  explicit publication/deployment authority.
