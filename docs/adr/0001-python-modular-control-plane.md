# ADR 0001: Python modular control plane

- Status: Accepted
- Date: 2026-08-22

## Context

The source blueprint proposes Python for intelligence and Java for some enterprise control-plane concerns. The project requirement explicitly prefers Python and asks to avoid Java. An immediate microservice topology would also make transactional lifecycle and policy correctness harder before independent scaling is justified.

## Decision

Use Python 3.12+ for the full backend. Implement the initial system as a modular control plane with explicit domain packages, repository interfaces, application services, events, and no cross-domain table access. Expose versioned HTTP/event contracts and design modules so high-throughput domains can later be extracted without rewriting callers.

FastAPI provides the App Server boundary, Pydantic defines contracts, SQLAlchemy/Alembic manage persistence, and asyncio supports model and connector concurrency.

## Consequences

The project has one backend language and a simpler contributor path. Correctness-sensitive IAM and policy code requires strict typing, review, property tests, and security tests. Service extraction remains possible but is based on measured load and ownership, not speculative topology.
