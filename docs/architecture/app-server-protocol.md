# App Server protocol

## Purpose and boundary

Obsion App Server is the single bidirectional protocol boundary between Experience
clients and the Harness. Web, IDE, CLI, HTTP API, and IM adapters must not implement
their own Thread or Run semantics. The App Server translates a versioned client
protocol into the same application services used by the REST API; it is not a second
runtime and it never bypasses workspace authorization, policy, approval, audit, or
Evidence controls.

The public protocol is WebSocket plus JSON-RPC 2.0 at
`/api/v1/app-server`. REST remains the management and binary-transfer surface. In
particular, large Artifact upload/download stays on REST, while Artifact metadata and
Run lifecycle operations are available over the App Server. Internal service splits
may add gRPC later without changing the public protocol.

## Version and connection lifecycle

Clients must offer the `obsion.jsonrpc.v1` WebSocket subprotocol. Browser origins are
checked against the configured allow-list before the connection is accepted;
non-browser clients may omit `Origin`.

After accepting a connection the server sends a `server.ready` JSON-RPC notification.
The first client request must be `server.initialize` with:

- exact protocol version `2026-08-26`;
- a bounded client name and version;
- an optional bearer token when it could not be supplied in the HTTP `Authorization`
  header.

Development authentication still resolves the configured development principal.
It does so only after an explicit configured development bearer credential is supplied;
there is no missing-token fallback. OIDC deployments accept a bearer token in the
handshake header or in the initialize request. Tokens are consumed only by
authentication and are never copied into events, audit metadata, telemetry, errors,
or retained request records. No other method is processed before successful
authentication and initialization.

A Workbench browser may omit the initialize bearer only when the WebSocket handshake
contains the revocable opaque session cookie previously issued by
`POST /api/v1/auth/session`. The application facade resolves that cookie through the
same provisioned Principal loader as REST; the transport does not query session rows.
An explicit header/initialize bearer takes precedence. Browser Origin validation still
occurs before the socket is accepted, so cookie authentication does not weaken the
connection boundary.

The server returns the authenticated principal identity, negotiated limits, and
available method groups. A connection has one immutable principal and tenant for its
entire lifetime. Authentication changes require a new connection.

## JSON-RPC contract

Each text frame contains one JSON-RPC 2.0 request object. Batch requests and binary
frames are rejected deliberately so that message limits, ordering, and resource use
remain predictable. Request IDs are non-null strings or integers. Params are objects;
unknown fields are rejected.

Standard errors use `-32700`, `-32600`, `-32601`, `-32602`, and `-32603`.
Domain failures occupy the JSON-RPC server range and include a stable Obsion code,
safe details, HTTP-equivalent status, and correlation ID in `error.data`. Internal
exceptions expose only a correlation ID.

Client notifications never execute mutations. The only accepted client notification
is `server.ping`; all lifecycle commands require an ID and produce a response. This
prevents a caller from losing the outcome of a side effect.

## Methods

The version-one method surface is grouped by the blueprint's App Server resources:

- server: `server.initialize`, `server.ping`;
- workspace context: `workspace.list`;
- Thread: `thread.list`, `thread.create`, `thread.archive`, `thread.resume`,
  `thread.fork`, `thread.turns`, `thread.runs`, `thread.events`;
- Turn: `turn.create`;
- Run: `run.get`, `run.cancel`, `run.replay`, `run.events`, `run.subscribe`,
  `run.unsubscribe`;
- Approval: `approval.list`, `approval.decide`;
- Artifact: `artifact.list`, `artifact.get`.

Lifecycle mutations use the same transactional application services, event store, and
audit writer as REST. Workspace creation, registry administration, connector setup,
bulk export, and binary content remain REST management operations rather than being
duplicated in the realtime protocol.

The WebSocket and JSON-RPC packages are transport adapters only. They may validate
frames, map typed params, authenticate through the application facade, and project
results, but they cannot open database sessions, execute queries, construct an Event
store, or invoke a model/Harness runtime. `AppServerApplication` owns request
transactions and delegates domain behavior to the same application services as REST.
A static architecture test rejects persistence, Harness, and Model Gateway imports in
the App Server transport package.

Fork is a branch operation, not a mutable alias. Creating a fork transactionally
archives its source Thread and emits `thread.archived`; subsequent source Turn writes
fail until an authorized caller explicitly resumes the source. The child observes a
frozen effective history at `forked_from_turn_id`. Replaying a Run creates another Run
for the same Turn, preserving the `Turn 1 → Run N` cardinality.

## Durable idempotency

Every persistent mutation requires a caller-generated `client_request_id`. The server
stores an organization- and principal-scoped record containing the method, canonical
validated-params fingerprint, and final result or domain error. The idempotency claim,
business mutation, event/audit append, and recorded outcome commit in one database
transaction.

A retry with the same principal, key, method, and params receives the recorded outcome
without repeating the mutation. Reusing a key for another method or payload is a
conflict. The identity fields and completed outcome are database-protected from
updates; records cannot be deleted before their retention deadline. This makes
reconnection retry safe across processes and deployments, not merely within one
WebSocket connection.

## Run event cursors and subscriptions

Aggregate `Event.sequence` orders facts inside one aggregate. A Run stream can also
contain events whose primary aggregate is an Artifact or another resource, so it has a
separate immutable `Event.run_sequence`. The sequence is allocated under the Run row
lock in the same transaction as the event. `Run.aggregate_version` is the durable next
Run-event position.

`run.events` and all streaming cursors use `run_sequence`; Thread event queries keep
using aggregate `sequence`. This separation is required to prevent duplicate cursors
and skipped cross-aggregate facts.

`run.subscribe` first authorizes the Run and returns a subscription ID and accepted
cursor. Only after that response is sent may event notifications begin. Each event is
emitted as a JSON-RPC notification whose method is the domain event name, such as
`answer.delta`, `tool.started`, `tool.completed`, `approval.requested`, or
`plan.updated`. Params contain the subscription ID and complete Event view.

One bounded poller multiplexes all subscriptions on a connection. It reauthorizes each
Run, advances only after a frame is sent, emits heartbeats with current cursors, and
ends a caught-up terminal Run with `run.subscription.completed`. A client reconnects
with its last processed Run sequence—even on a new connection—for at-least-once
delivery. Consumers therefore deduplicate by Event ID and persist the cursor only
after processing. The SSE compatibility projection accepts `after` and
`Last-Event-ID`, uses the newer cursor, and reads the identical Run stream.

Cancellation is a terminal application command, not a UI flag. It serializes on the
Run, records the original state, clears its lease, cancels all active Steps, appends
`run.cancellation_requested` followed by `run.cancelled`, and writes audit in one
transaction. Harness scheduling and Step completion use the same Run-before-Step lock
order. An external call that already started may finish cooperatively, but it cannot
reopen the Run, overwrite cancelled Steps, start a dependent Step, or publish an
answer/completion after cancellation.

## Security and operating invariants

- Origin checks, required subprotocol, initialization timeout, frame-size limit, and
  subscription limit are configuration-backed.
- One send lock serializes responses and asynchronous notifications so frames cannot
  race on a connection.
- Every subscription is reauthorized while active; revoked access terminates that
  subscription with a safe error notification.
- Tenant IDs and principal IDs come only from the authenticated Principal, never from
  request params.
- Domain errors are safe and structured; raw exceptions and tokens are never sent.
- Binary Artifact content and large uploads do not pass through JSON-RPC.
- REST/SSE and WebSocket are additive adapters over one Harness and one event model.

## Compatibility

Protocol changes are additive within `obsion.jsonrpc.v1`. Breaking changes require a
new WebSocket subprotocol and protocol version. Event payloads retain their own
`schema_version`; clients must ignore unknown additive fields and event methods.
Server method discovery is returned by `server.initialize` so SDKs can fail clearly
when connected to an incompatible deployment.
