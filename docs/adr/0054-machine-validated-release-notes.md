# ADR 0054: Operator release notes are machine-validated contracts

- Status: Accepted
- Date: 2026-08-30

## Context

Phases 68-74 completed DingTalk/WeCom HTTP delivery, WeCom callback decrypt,
public vendor ingress, DingTalk/WeCom Knowledge sources, shared vendor Knowledge
budgets/provenance, and Workbench citation provenance. Operator documents still
contained pre-Phase-68 claims that DingTalk/WeCom HTTP was rejected and WeCom
ciphertext could not be decrypted. A prose-only changelog did not make this drift
detectable in CI. The Phase 25 SBOM helper also retained a hard-coded
`0.25.0-dev` component version after the project status advanced.

## Decision

Each consolidated operator release may provide a versioned `ReleaseNotes` YAML
contract next to its human-readable Markdown notes. The control-plane release module
validates:

- semantic version and contiguous preceding phases;
- explicit database migration mode and revision list;
- repository-local referenced documents;
- unique vendor ids, bare HTTPS origins, and environment-variable names without
  values;
- non-empty architecture boundaries, rollout, rollback, verification, and known
  limitations.

`obsion validate-release-notes` is a root and CI quality gate. The current manifest
is `docs/release/0.75.0-dev.yaml`. Source-contract tests also compare its origins,
connector identities, operations, and environment names with implementation files
and connector examples.

CycloneDX generation accepts an explicit component version, and the CLI reads the
authoritative version from `docs/project-status.yaml` unless the operator supplies an
override. Release metadata does not create a second runtime configuration system.

## Consequences

Operator support claims, migration posture, and pinned vendor origins can no longer
drift silently from the release contract. The manifest stores no credential value,
does not enable an integration, and does not replace staging or human approval.

Phase 75 adds no database schema revision. Future release manifests must update their
own versioned path and CI default intentionally; marketplace discovery, vendor app
provisioning, and a second control-plane language remain out of scope.
