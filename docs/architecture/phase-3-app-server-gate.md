# Phase 3 App Server API freeze review

## Review question

The human gate asks whether the Phase 3 REST and `obsion.jsonrpc.v1` lifecycle surface
is suitable as the compatibility baseline for later Workbench, CLI, IDE, desktop, and
IM clients. Automated completion does not answer that organizational question and
does not create a signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Frozen lifecycle surface

- REST creates Workspaces, Threads, and Turns; reads Runs and Run Events; cancels
  Runs; and archives, resumes, or forks Threads.
- JSON-RPC exposes the same Thread/Turn/Run application behavior and adds durable
  principal-scoped idempotency plus resumable Run event subscriptions.
- A Turn may own multiple Runs. Replay appends a new Run and never replaces the prior
  execution.
- Fork creates a child at a fixed Turn boundary and archives the source as read-only.
  An explicit resume is required before any later source Turn.
- Lifecycle events use the one versioned Event registry and share the REST envelope,
  Outbox record, and WebSocket projection.

## Architecture evidence

The protocol adapters under `obsion.app_server` contain no database, persistence,
Harness, or Model Gateway dependency. They delegate to `AppServerApplication`, which
owns transactions and invokes the shared application services. The static boundary
test fails on forbidden imports, database primitives, Event Store construction, or
model construction in the transport package.

The lifecycle acceptance tests prove idempotent retries, tenant isolation, fork
lineage, source read-only behavior, explicit resume, manual active-Run archive
protection, one-Turn/multiple-Run replay, and cursor-resumable Event delivery.

## Human review checklist

- Confirm resource and method names are acceptable as the long-lived client surface.
- Confirm fork-as-source-archive is the intended branch semantics.
- Confirm `client_request_id` is required on every persistent JSON-RPC command.
- Confirm breaking protocol changes will use a new subprotocol/version rather than
  silently changing `obsion.jsonrpc.v1`.
- Record approver identity, decision, and date only through the project’s real review
  process.
