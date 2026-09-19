# H03 report: authorized resource catalog and enterprise relation map

Date: 2026-09-19
Baseline: `4295fb699b1fc15ad26ae6c6dabb16bfe26bf0b9`
Validated candidate: `6ede6ddce833db5bcedb666af9bd77228a91c42e`
Architecture gate: [authorization-first enterprise catalog](../architecture/phase-h03-authorized-catalog-gate.md)
Decision: **COMPLETE**

## Outcome

The Python control plane now exposes one authorization-first discovery surface for
business resources, current capabilities, and provenance-bearing enterprise
relations. PostgreSQL remains the source of truth; existing capability, code, and
semantic registries are projected rather than copied. The catalog overlay stores
business domains, services, logs, deployments, physical data clusters, and other
facts that do not already have a durable registry home.

Each resource carries an optional Owner and version, explicit source and source
version, observation time, validity, verification level, classification, state, and
required permission. Missing values remain absent. Each relation carries endpoint
versions, provenance, an evidence category, validity, and one of DECLARED,
STATIC_INFERENCE, or RUNTIME_OBSERVED. The database rejects a logical-data-source to
physical-cluster relation unless deployment evidence records a runtime observation.

Discovery evaluates `catalog.discover`, native ACL/grant outcomes, item permissions,
and resource-specific Policy rules before full descriptions, schemas, source paths,
connector details, or ranking text are loaded. Search and relation expansion operate
only on the authorized projection. The current Principal is always authoritative;
connector service-account grants cannot substitute for individual permission or ACL.

Capability results include exact input/output JSON Schemas, risk, side effect,
permission, safe connector dependencies, executable environments, evidence
categories, health validity, and a declared-timeout cost basis. READY,
UNCONFIGURED, STALE, and UNAVAILABLE are distinct from top-level EMPTY and
UNAUTHORIZED. Only `registry.write` principals receive credential-free repair
actions. Connector endpoint, configuration, credential reference, egress policy,
grant list, and private health diagnostics never enter the response.

The existing `/capabilities` contract remains compatible but now evaluates the Policy
Engine from a minimal envelope before loading descriptor text or schemas. Both old and
new discovery paths select the latest capability version and never fall back to an
older version with weaker permission.

## Requirements traceability

| Requirement | Implementation | Validation |
|---|---|---|
| H03-T01 governed business resource catalog | `CatalogResource`, existing code/semantic registry projections, nullable factual Owner/version and explicit freshness/state | persisted resource, code API/logical source, table, metric and repository projection tests |
| H03-T02 provenance-bearing relation map | `CatalogRelation`; service–deployment–source, repository–API, data source–table and metric–table edges | cross-tenant chain, dynamic registry projection and physical-mapping constraint tests |
| H03-T03 complete capability descriptor | catalog capability view with schemas, risk, environments, evidence, permission, dependency, freshness and declared timeout cost | stale capability and built-in API vertical-slice tests |
| H03-T04 authorize before retrieve/rank | minimal envelopes, Policy receipts, native ACL facts, authorized-only ranking and relation expansion | tenant isolation, denied ACL, generic unauthorized and old API regression tests |
| H03-T05 actionable explicit states | RESULTS/EMPTY/UNAUTHORIZED plus READY/UNCONFIGURED/STALE/UNAVAILABLE; admin-only safe repairs | full state matrix, expired-vs-empty and secret non-disclosure tests |

## Local validation

- H03 unit/API acceptance: PASS — 11 tests cover all four acceptance scenarios,
  connector states, latest-version permission hardening, existing registry projection,
  the protected HTTP vertical slice, and system-role boundaries.
- Pre-hardening CI-equivalent Python suite excluding the separately exercised
  distribution test: PASS — 3,030 passed, 286 explicitly skipped, 80.28% coverage
  in 497.57 seconds. The final bounded two-hop hardening then passed all 11 focused
  H03 tests locally and the complete remote quality gate.
- Contract/static analysis including the isolated distribution contract: PASS — 35
  tests.
- Ruff, repository formatting (1,136 files), strict mypy across 294 source files,
  contract/status/evaluation/release validators, secret scan, OpenAPI equality, and
  repository diff checks: PASS. Secret scan reported zero findings.
- JavaScript workspaces: PASS — lint, typecheck, production build, and 297 tests
  across Desktop, IDE, Web, and TypeScript SDK.
- Disposable PostgreSQL 17 full integration: PASS — upgrade to
  `d6e8f0a2b4c6`, 421 passed, 11 independent destructive-migration skips, and zero
  Alembic drift in 239.50 seconds.
- Isolated H03 PostgreSQL migration: PASS — empty upgrade/downgrade/re-upgrade,
  existing system-role permission migration, nonempty-catalog downgrade refusal, and
  final schema check passed.
- GitHub Actions final-candidate run
  [35449883526](https://github.com/SongJok/obSion/actions/runs/35449883526): PASS —
  all 16 jobs completed successfully, including quality, full PostgreSQL integration,
  H03 and legacy migration round trips, Java SDK, Helm, and containers.

## Acceptance scenarios

| Scenario | Local result | Receipt |
|---|---|---|
| H03-A01 same term in two tenants | PASS | Separate organizations return only their own service, deployment SHA, repository, capability version and relation provenance. |
| H03-A02 connector service account but no individual ACL | PASS | Connector grants contain the operation, while a non-ACL Principal receives EMPTY and no description; the allowed Principal sees the resource. |
| H03-A03 expired connection versus real empty | PASS | The matching descriptor is STALE with a refresh action; a nonmatching authorized query is EMPTY with no repair action. |
| H03-A04 logical database without deployment evidence | PASS | Only the logical source is returned; no physical cluster or edge appears, and a static CODE mapping is rejected by the database. |

## Migration and rollback

Alembic revision `d6e8f0a2b4c6` creates `catalog_resources` and
`catalog_relations`, their tenant/search indexes, validity checks, and the physical
mapping evidence guard. It grants `catalog.discover` to existing non-admin system
roles without changing any business fact or manufacturing catalog rows.

An empty catalog can downgrade to `c5d7e9f1a3b5`. Downgrade refuses to drop populated
catalog tables, so operators must first export/reconcile facts under an explicit data
retention decision. The harmless permission string remains on rollback because older
code does not interpret it and removing a pre-existing/custom grant would be unsafe.

## Boundaries and follow-on

- No production catalog fact, connector credential, external call, or human approval
  was fabricated or executed.
- H03 discovery does not execute a capability. Capability Gateway and Policy Engine
  remain mandatory at execution time.
- The initial relation map uses PostgreSQL and bounded two-hop expansion, not a graph
  database.
- H04 may consume this authorized projection for model-native understanding, proposal
  generation, dynamic planning, and deterministic fallback.
