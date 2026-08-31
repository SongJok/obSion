# ADR 0004: Governed static Code Graph

- Status: Accepted
- Date: 2026-08-29

## Context

Engineering questions require symbol, reference, and call-chain answers grounded in
authorized source, not unrestricted repository execution or a write-capable Git
integration. Phase 18 already exposed read-only `git.*` and `code.search` HTTP
contracts. A durable Code Graph is still required so EngineeringAgent can cite
immutable snapshots through the Capability Gateway.

## Decision

Obsion stores a tenant-scoped Code Graph in PostgreSQL. Ingestion parses uploaded
source with static analyzers (Python `ast`, conservative Java/TypeScript scans) and
never executes repository code. Each successful index creates an immutable snapshot.
Repository ACLs are normalized into allow/deny grants and applied before ranking.
`code.symbol`, `code.reference`, `code.callers`, and `code.callees` are INTERNAL
capabilities bound to `obsion-code-index`. Missing authorized CODE Evidence produces
an explicit unknown answer.

## Consequences

Call-chain answers become Evidence-backed and replayable. Parser coverage is bounded
and conservative for non-Python languages. Broad production Git write paths, auto-PR,
and executing untrusted source remain out of contract.
