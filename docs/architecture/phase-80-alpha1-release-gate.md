# Phase 80 Alpha.1 repository release review

## Review question

Can the first Alpha.1 candidate prove that one repository revision contains a
continuous implementation/report/architecture history, one linear PostgreSQL
migration chain, a matching SBOM, green quality gates, and honest operator-owned
limitations—without publishing externally or claiming production approval?

**Status: PENDING — automated evidence does not constitute a signed release,
staging/UAT, security approval, or data-owner acceptance.**

## Delivery contract

- `docs/release/0.80.0-alpha.1.md` is the human candidate contract.
- `docs/release/0.80.0-alpha.1.yaml` consolidates Phases 1–79 and Phase 80 validates
  the release bundle itself.
- Project status, phase reports, architecture reviews, Alembic chain, SBOM, vendor
  names/origins/environment-variable names, rollout, rollback, verification, and
  limitations are machine checked.
- Retrospective Phase reports disclose their reconstruction and do not invent old test
  counts or human decisions.
- The legacy `0.75.0-dev` manifest remains valid when selected explicitly.
- Publication metadata remains `alpha`, `externallyPublished: false`, and
  `signedTag: false`.
- No production write, generic HTTP delivery, alternate Harness, Kafka/ClickHouse base,
  credential material, or Java backend is introduced.

## Automated acceptance map

- `test_phase80_alpha1_release.py` proves repository completeness, project/SBOM/version
  alignment, linear Alembic ancestry, Knowledge-only vendor support, CLI default, and
  fail-closed drift behavior.
- `test_phase75_release_notes.py` preserves the earlier release contract.
- `make check`, JDK 21 SDK tests, PostgreSQL invariant/migration tests, and the explicit
  non-writing Feishu browse provide the current revision's verification matrix.
- Secret scanning covers every new release/report file and environment-variable values
  are never placed in the manifest.

## Migration review

Phase 80 adds no database revision. The Alpha.1 contract declares all 30 existing
revisions from `241e275bde59` through `a79c4d2e8f10` and the validator proves one base,
one head, no branch/merge, and exact ancestry. Rollback is restore-and-redeploy, not a
blind production downgrade.

## Human review checklist

- Confirm whether maintainers authorize an external Alpha.1 tag/package/image in a
  separate release action.
- Reproduce from a clean checkout and verify generated SBOM/image digests.
- Run clean staging, UAT, timed backup/restore, live OIDC/secret manager, and required
  tenant/data-owner/security checks.
- Keep every production write/deploy/restart and unsigned marketplace path denied.
