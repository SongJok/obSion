# Project governance

Obsion is an open-source project governed in the open. Technical direction is
documented through architecture decision records (ADRs), and product scope is tracked
in the requirements traceability matrix.

## Roles

- **Contributors** submit issues, documentation, tests, code, or reviews.
- **Maintainers** review changes, steward releases, respond to security reports, and
  protect compatibility and safety boundaries.
- **Committers** are maintainers trusted to merge changes after review and automated
  quality gates pass.

Role changes are based on sustained, constructive participation and are recorded in a
public project decision. No single organization receives special technical authority
through commercial use or sponsorship.

## Decisions

Routine changes use pull-request consensus. Changes to public contracts, persistence,
security posture, governance, or V1 scope require an ADR and approval from at least two
maintainers. When consensus cannot be reached, maintainers publish the alternatives and
their reasoning before a simple majority decision. A maintainer with a conflict of
interest must recuse themselves.

## Releases

Obsion follows Semantic Versioning. Release candidates must pass CI, database migration
checks, security-boundary tests, and the documented acceptance scenarios. A release
includes a changelog, signed source tag when signing infrastructure is available, and
reproducible container build instructions.

## Changes to governance

Governance changes use the same public ADR process and require a two-thirds majority of
active maintainers after a minimum seven-day review period.
