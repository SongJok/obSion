# H03 architecture gate: authorization-first enterprise catalog

Date: 2026-09-19
Decision: **PASS**
ADR: [0145](../adr/0145-authorization-first-enterprise-catalog.md)
Validated candidate: `6ede6ddce833db5bcedb666af9bd77228a91c42e`

## Boundary reviewed

H03 extends the existing Python control plane, PostgreSQL schema, registry models,
Policy Engine, code graph, and semantic catalog. It adds no second Harness, backend
language, graph database, external credential path, or model-side authorization.
Capability execution remains exclusively behind Capability Gateway.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| Tenant boundary precedes discovery | PASS | Every catalog, code, semantic, capability, connector and relation query is organization-scoped; A01 returns disjoint IDs and provenance for the same term. |
| Policy precedes full descriptor retrieval | PASS | Minimal envelopes contain only authorization inputs; full descriptions, schemas, source paths and connector state load only for ALLOW decisions. |
| Native ACLs remain authoritative | PASS | Code repository grants and catalog ACL outcomes enter `ResourcePolicyInput.resource_access_allowed`; explicit deny wins even for otherwise privileged principals. |
| Connector identity cannot grant user access | PASS | Principal permission and ACL are evaluated independently of connector grants; A02 proves a configured service account cannot reveal the resource. |
| Hidden resources do not affect ranking/errors | PASS | Search and relation expansion consume only allowed candidates; denied/unknown queries return the same bounded EMPTY shape and UNAUTHORIZED is generic. |
| Capability descriptors are selection-ready | PASS | Schemas, risk, side effect, permission, evidence categories, environment, dependency state, validity and declared timeout basis are present without credentials. |
| Operational states are not collapsed | PASS | RESULTS/EMPTY/UNAUTHORIZED and READY/UNCONFIGURED/STALE/UNAVAILABLE have separate tested paths; repairs are `registry.write` only. |
| Relations are facts, not guesses | PASS | All edges have endpoint versions, source/ref/version, evidence kind, observed time, validity and verification; static versus observed is explicit. |
| Physical cluster mapping requires deployment evidence | PASS | Database check requires RUNTIME_OBSERVED plus DEPLOYMENT; A04 rejects a source-code-only mapping and emits no inferred edge. |
| Existing registries remain source of truth | PASS | Repository/API/logical source and data source/table/metric records are projected; persistent overlays cannot be bypassed by a duplicate dynamic key. |
| Latest capability permission cannot downgrade | PASS | Discovery fixes the latest version before authorization; a denied v2 does not expose an allowed v1. The compatibility API uses the same ordering. |
| Credentials remain outside model/API context | PASS | Response schemas cannot represent endpoint/configuration/credential/grant/egress/private-health fields; negative tests scan serialized responses. |
| Migration is loss-aware and drift-free | PASS | PostgreSQL 17 upgrade/check and isolated round trip pass; downgrade refuses a populated catalog. |
| Full local candidate gates | PASS | 3,030 Python tests, 80.28% coverage, 421 PostgreSQL tests, 297 JavaScript tests, strict typing/format/contracts and zero secret findings pass. |
| Remote candidate gates | PASS | GitHub Actions run [35449883526](https://github.com/SongJok/obSion/actions/runs/35449883526) completed all 16 quality, PostgreSQL, migration, SDK, Helm, and container jobs successfully. |

## Acceptance receipts

| Acceptance | Result | Security property |
|---|---|---|
| H03-A01 | PASS | Tenant, capability and edge provenance isolation |
| H03-A02 | PASS | End-user ACL cannot be replaced by connector service identity |
| H03-A03 | PASS | Expired connectivity is actionable STALE, not fabricated EMPTY |
| H03-A04 | PASS | Static logical names cannot become physical production facts |

## Migration and rollback

Revision `d6e8f0a2b4c6` is the sole H03 schema change. Empty downgrade is tested;
populated downgrade fails closed to preserve catalog facts. Existing business data is
not backfilled, and the migration does not claim freshness, ownership, deployment, or
physical topology.

## Follow-on

H04 can rank proposals only after receiving this governed projection. H05 owns live
dimension sufficiency and stop rules. H11 owns deeper runtime observation, and H12
owns durable waiting/recovery when a required connection or permission is missing.
