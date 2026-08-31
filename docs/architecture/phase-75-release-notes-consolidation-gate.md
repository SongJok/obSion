# Phase 75 release-note consolidation review

## Review question

Can an operator determine exactly what Phases 68-74 support, configure and roll out
the three vendor paths without exposing credentials, and roll them back without
inventing a migration or losing audit evidence—and can CI detect future documentation
drift?

**Status: PENDING — automated checks do not constitute production, staging, live
vendor tenant, or security approval.**

## Delivery contract

- `docs/release/0.75.0-dev.md` is the human operator contract.
- `docs/release/0.75.0-dev.yaml` records the same phase range, origins,
  environment-variable names, migration posture, rollout, rollback, and limits.
- `obsion validate-release-notes` fails closed on non-contiguous phases, unsafe
  origins, credential-like values, missing documents, and incomplete procedures.
- Root `make check` and CI execute the validator.
- SBOM component version comes from `docs/project-status.yaml`; it is no longer
  pinned to Phase 25.
- Administrator, runbook, deployment, upgrade, developer, connector, roadmap, README,
  and changelog copy match implemented Feishu/DingTalk/WeCom support.
- Phases 68-75 add no Alembic revision; no empty migration is fabricated.
- Experience IM remains outside Harness and Knowledge remains behind Capability
  Gateway/Policy/ACL/Audit.

## Automated acceptance map

- `test_phase75_release_notes.py` validates the repository manifest, vendor source
  origins, connector examples, environment names, operator copy, SBOM version, CLI
  registration, and fail-closed malformed manifests.
- `uv run obsion validate-release-notes` validates the current release document set.
- Existing vendor IM/Knowledge phase tests remain regression coverage.
- Secret scan ensures the new operator files contain names only.

## Migration review

No data-model or Event contract changes are made. The manifest explicitly declares
`database: none` with an empty revision list. Operators still run Alembic drift checks.

## Human review checklist

- Validate vendor app scopes against an actual tenant without copying secrets into
  tickets, source, command output, or model context.
- Validate public DNS/TLS/callback registration and exact network egress in staging.
- Validate allowed/denied ACL retrieval and citation provenance with tenant-owned
  documents.
- Preserve delivery receipts, Evidence, provenance, Events, and Audit during rollback.
- Staging deployment, timed DR drill, and human security/data-owner sign-off remain
  operator-owned from Phase 25.
