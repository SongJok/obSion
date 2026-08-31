# ADR 0059: Alpha.1 is a repository-evidenced candidate, not a production claim

- Status: Accepted
- Date: 2026-08-31

## Context

Obsion has accumulated 79 completed implementation phases across the durable Harness,
Capability/Policy/Evidence boundaries, Knowledge/Data/Engineering scenarios,
Workbench/SDK clients, automation/actions, and vendor integrations. The only
machine-readable release contract covered Phases 68–74 at `0.75.0-dev`; it could not
prove that the current project status, phase reports, architecture reviews, Alembic
history, and SBOM described one repository revision. Historical Phase 1–14 and 16–20
reports were also absent even though their implementations, architecture packets, and
tests existed.

## Decision

Publish a repository-local `0.80.0-alpha.1` candidate contract in human-readable and
machine-readable form. The manifest consolidates Phases 1–79 and is completed by Phase
80. Its validator additionally requires:

- project version, current phase, and a gap-free completed-phase list to match;
- exactly one report and one architecture review for every Phase 1–80;
- the declared Alembic revisions to equal the repository's single base-to-head chain;
- the CycloneDX component version to equal the release version;
- an explicit publication stage and false external-publish/signed-tag claims;
- vendor contracts with at least one Experience or Knowledge surface, allowing
  Knowledge-only Confluence without inventing an IM integration.

Missing historical reports are reconstructed as clearly labelled retrospective
records. They cite current release validation and the original architecture gate but
do not invent original test counts, dates, or human approvals.

## Consequences

`obsion validate-release-notes` now defaults to the Alpha.1 manifest, while the
`0.75.0-dev` contract remains independently valid and reproducible. Alpha.1 does not
publish a Git tag, image, package, or external artifact and is not a signed production
release. Staging, UAT, DR timing, live OIDC, permitted tenant data, and human
security/data-owner approval remain outside repository automation.

No runtime, Event, API, database, Agent, Capability, production write, or credential
boundary changes in this ADR.
