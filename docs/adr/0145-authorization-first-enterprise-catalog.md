# ADR 0145: authorization-first enterprise resource catalog

- Status: Accepted
- Date: 2026-09-19
- Work package: H03
- Supersedes: none

## Context

The control plane already has authoritative registries for capabilities, connectors,
semantic data, code snapshots, and knowledge ACLs. It did not have one safe discovery
projection that could answer what resources exist, how resources are related, and
which capabilities are currently executable without first exposing every tenant
description to a model.

A parallel graph store or copied universal inventory would create another source of
truth and another authorization path. Treating connector credentials or a connector's
service account as end-user authority would also violate the Principal and Policy
boundary. Finally, inferring a physical production cluster from a source-code logical
name would turn an exploration hypothesis into a false enterprise fact.

## Decision

Add a PostgreSQL-backed catalog overlay and an authorization-first projection service
inside the existing Python control plane.

1. Existing capability, code, and semantic registries remain authoritative. The
   catalog projects their current records at read time rather than copying them.
2. `catalog_resources` stores only resource kinds without an existing durable home,
   or explicit catalog metadata. A fact carries kind, canonical key, optional Owner
   and version, state, classification, required permission, source reference, source
   version, observation time, validity, and verification level. Missing values remain
   `null`; the catalog does not invent an Owner, version, freshness, or deployment.
3. `catalog_relations` stores typed edges with both endpoint versions and explicit
   provenance, evidence category, validity, and verification level. Static inference,
   declaration, and runtime observation are separate values. A logical-data-source to
   physical-cluster edge is rejected at the database boundary unless it is a runtime
   observation backed by deployment evidence.
4. Discovery first evaluates the generic `catalog.discover` gate. It then loads only
   minimal authorization envelopes, submits native ACL/grant outcomes to the Policy
   Engine, and loads descriptions, schemas, source locations, connector state, and
   ranking text only for allowed records. Search and deterministic two-hop relation
   expansion run over that authorized set. Hidden rows do not affect result counts or
   error text.
5. Connector configuration, endpoints, credential references, egress policy, grants,
   and private health diagnostics never enter the discovery response. A connector's
   grants cannot replace the current Principal's permission or resource ACL.
6. Capability descriptors expose their exact input/output schemas, risk, side effect,
   required permission, executable environments, evidence categories, safe connector
   dependency state, declared timeout cost basis, and health validity. They distinguish
   READY, UNCONFIGURED, STALE, and UNAVAILABLE. Top-level discovery separately returns
   RESULTS, EMPTY, or UNAUTHORIZED.
7. Only principals with `registry.write` receive credential-free repair actions. All
   system roles may enter discovery; resource-specific permissions and ACLs still
   determine each visible item.
8. The compatibility `/capabilities` endpoints keep their response contract but now
   use the same Policy Engine decision before loading full descriptor text or schemas.
   Selection always evaluates the latest capability version; it never falls back to an
   older version with a weaker permission.

## Consequences

- PostgreSQL remains the transactional source of truth; no graph database, second
  service, second backend language, or model-side authorization logic is introduced.
- Every returned resource, capability, and relation has a durable PolicyDecision
  receipt. Read-only discovery therefore writes audit evidence.
- Business domains, services, logs, deployments, and physical-cluster facts require a
  verified catalog producer. Their absence is visible as missing data, not filled by a
  model guess.
- Existing production system-role rows receive `catalog.discover` during migration.
  Downgrade intentionally leaves that inert permission in place, and refuses to drop
  populated catalog tables to prevent silent fact loss.
- H04 may consume this authorized projection for model-native planning. This ADR does
  not make an unavailable capability executable and does not bypass Capability Gateway
  at execution time.

## Alternatives rejected

- **Feed the complete registry to the model and instruct it to hide unauthorized
  rows.** Rejected because descriptions and ranking already leak before generation.
- **Use connector service-account grants as user authorization.** Rejected because it
  collapses connector authentication and individual authorization.
- **Infer production mappings from repository configuration or naming conventions.**
  Rejected because static source evidence does not prove runtime deployment state.
- **Introduce a graph database.** Rejected for H03 because PostgreSQL tables and the
  existing registry indexes support the required bounded projection without another
  operational truth store.
