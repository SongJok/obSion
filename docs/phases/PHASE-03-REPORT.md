# PHASE-03-REPORT — Unified App Server lifecycle

> Retrospective Phase 80 record based on the frozen App Server contract and current
> regression suite; it does not create a protocol approval.

## Delivered

- Exposed Workspace, Thread, Turn, Run, Approval, Artifact, and Event behavior through
  one REST/application facade and `obsion.jsonrpc.v1` adapter.
- Added durable principal-scoped mutation idempotency, resumable Run cursors,
  create/resume/fork/archive lifecycle, and one-Turn/multiple-Run replay.
- Kept transports free of persistence, Harness, and Model Gateway ownership.

## Migration and validation

App Server durable request/cursor state is represented in the linear Alembic history,
including revision `d5e8f9012abc`. Phase 80 revalidated protocol parsing, concurrency,
tenant isolation, reconnect, SDK, and static layer boundaries.

## Remaining boundary

Breaking wire changes require a new protocol version; live multi-node WebSocket
acceptance remains an operator deployment concern.
