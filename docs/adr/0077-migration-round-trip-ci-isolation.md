# ADR 0077: Isolated CI for every destructive migration round trip

- Status: accepted
- Date: 2026-09-03
- Phase: 97 amendment

## Context

Obsion keeps four PostgreSQL migration tests opt-in because each test changes
schema history through an upgrade, downgrade, and re-upgrade. CI already gave
the audit-log rename and Phase 2 identity migrations dedicated databases, but
the Phase 5 browser-session and Phase 79 operator-invocation tests were only
collected by the shared migrations job. Their opt-in flags were never set, so a
green CI run skipped both round trips while the release documentation described
the migration suite as complete.

Running either test against the shared development database is not acceptable:
the downgrade can remove later schema and persisted data. Removing the opt-in
guard is also unsafe because the default test command deliberately supports
developer databases.

## Decision

1. CI owns a `migration-round-trips` matrix for Phase 5 auth sessions and
   Phase 79 operator Capability invocations.
2. Every matrix entry declares a unique PostgreSQL database, the exact opt-in
   environment variable, and one test path. The flag and path therefore cannot
   drift into separate workflow steps.
3. Matrix entries run on independent GitHub-hosted jobs with fresh PostgreSQL
   17/pgvector services. `fail-fast: false` preserves both results when one
   migration fails.
4. Each entry runs Alembic drift detection after its round trip.
5. The Alpha.1 container/candidate job depends on the complete matrix, so
   release artifacts cannot be built after a skipped or failed Phase 5/79
   migration proof.
6. Default local tests keep the opt-in guards. Operators may reproduce a case
   only in an explicitly disposable database.

## Consequences

- Every destructive migration test now has an isolated CI execution path.
- Shared developer and staging databases remain protected from test-driven
  downgrades.
- The change adds no runtime dependency, route, schema revision, credential, or
  production permission.
- The six operator-owned Alpha.1 promotion gates remain unchanged; CI evidence
  cannot substitute for staging/UAT, signatures, live identity infrastructure,
  or accountable human approval.
