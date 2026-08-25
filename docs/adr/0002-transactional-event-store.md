# ADR 0002: Transactional PostgreSQL event store

- Status: Accepted
- Date: 2026-08-22

## Context

Runs must be traceable, streamable, resumable, forkable, and replayable. Lifecycle state and emitted events must not diverge. Kafka adds valuable distribution but cannot by itself provide the transaction boundary for initial aggregate state.

## Decision

Persist append-only events in PostgreSQL in the same transaction as lifecycle state changes. Enforce a unique aggregate sequence and optimistic version. Stream committed events through resumable database cursors. Add an outbox projection so Kafka/ClickHouse can be introduced without changing domain semantics.

## Consequences

The source of truth is simple to operate and strongly consistent. Event retention and table partitioning need explicit operational policies. Scale-out consumers remain asynchronous projections and cannot mutate aggregate state directly.
