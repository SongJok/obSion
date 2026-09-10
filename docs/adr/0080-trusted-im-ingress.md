# ADR 0080: Trusted IM ingress is committed before asynchronous Harness dispatch

- Status: Accepted -- M1a control-plane implementation complete
- Date: 2026-09-08
- Milestone: M1a trusted IM ingress
- Amends: ADR 0010, ADR 0011, ADR 0047, and ADR 0078

## Context

The M1 product-entry contract requires a DingTalk message to be attributable to a
trusted installation, a real person, and an authorized conversation audience before
it can create a Harness task. The existing IM path has stable
`(channel, sender_id)` principal bindings, signed HTTP callback parsing, and an
authorized delivery ledger. It does not yet retain installation identity, provide a
durable inbound event record, or separate vendor acknowledgement from Run creation.

The current optional DingTalk Stream adapter can normalize a vendor callback and
preserve its event identity, but it still calls the legacy bridge synchronously. The
legacy bridge waits for a terminal Run result and sends the response from its own
process. Therefore a restart, a concurrent vendor redelivery, or an ambiguous
outbound response cannot yet satisfy M1's exactly-one-logical-task and no-blind-retry
requirements.

M1 must preserve the existing Python control plane, PostgreSQL as the transactional
source of truth, the Workspace -> Thread -> Turn -> Run -> Step -> Event Harness
model, and the Capability Gateway and Policy Engine enforcement boundaries. A vendor
Stream SDK is an Experience adapter; it is not a second control plane, a source of
authorization, or a way for an Agent to receive application credentials.

## Decision

### 1. Introduce a versioned trusted-input contract

M1a defines a `TrustedImEvent` accepted only after adapter validation. Its minimum
fields are:

- channel, installation identity, vendor event identifier, and event type;
- trusted `corp/app` identity used to resolve one active organization;
- stable sender identifier, conversation identifier, and bounded message text;
- canonical payload fingerprint, sanitized event timestamp, and audience descriptor;
- immutable links to the accepted Inbox event and, once dispatched, the resulting
  Thread, Turn, and Run.

The contract does not retain a raw vendor callback as an executable object. It stores
only the bounded, schema-validated values needed for idempotency, attribution,
auditing, and deferred dispatch. Display names, group nicknames, and unrecognized
vendor fields have no authorization weight.

### 2. Bind installations before accepting vendor events

An administrator provisions an active `ImInstallation` that maps the trusted DingTalk
`corp/app` identity to exactly one organization. Installation records include channel,
vendor installation identifiers, activation state, and audit metadata. They do not
contain application secrets or connector credentials.

Principal bindings and administrator-managed group/project audience bindings are
scoped through that installation. A legacy vendor binding that lacks installation
attribution remains historical data and enters a pending-remediation state; the
service must not infer its corporation or application from a sender identifier.
Existing development-channel behavior remains compatible, while a production DingTalk
event without an active installation fails closed before any task is created.

### 3. Commit the Inbox before ACK, then dispatch asynchronously

The control-plane acceptance endpoint resolves the installation, validates the
sender and audience shape, records an immutable PostgreSQL Inbox row, writes an
Audit record, and commits before the Stream or HTTP adapter acknowledges the vendor.
The acceptance transaction owns a unique `(installation_id, vendor_event_id)` key and
a canonical fingerprint.

- An exact replay returns the previously accepted result and creates no second task.
- Reuse of the same vendor event identifier with another fingerprint is rejected and
  produces no task.
- An unmapped, inactive, malformed, or unauthorized installation is rejected before
  an Inbox write that could be dispatched.

A worker role in the same Python control plane claims accepted Inbox rows using a
lease, heartbeat, and fencing token. It creates the user-owned Thread, Turn, and Run
through the existing Workspace/Harness services, records the durable links, and
emits auditable lifecycle events. A crash after the Inbox commit is recoverable by a
new worker; a stale worker cannot publish a duplicate result. The adapter returns a
quick acknowledgement after the committed acceptance, never after waiting for a
terminal Run.

### 4. Enforce intent and audience boundaries at admission and delivery

M1 admission classifies requests as query, summary, analysis, or create. Intent only
narrows the allowable behavior. Effective permission remains the intersection of the
user, application scopes, project/resource ACL, connector grants, Agent declaration,
task authorization, and environment/budget policy. Cancellation, clarification, and
help are conversation controls and do not grant a business capability.

In a group, task context is private to the bound user by default. Group/project
bindings are administrator-managed, and every knowledge, data, and Capability access
is authorized again for the user and resource. A group response is sent only after a
recipient-audience check confirms that its content is permitted for that audience. If
the check is absent, stale, denied, or inconclusive, the only permitted group response
is a content-free status and a link whose Web access is separately authorized.

### 5. Evolve the existing delivery ledger into a durable Outbox

M1b will extend the existing authorized `ImDelivery` ledger rather than create a
parallel delivery fact store. A delivery becomes eligible only after its answer,
audience, Policy decision, and content fingerprint are pinned. The outbox worker
claims delivery attempts with a lease, bounded backoff, rate-limit awareness, and
auditable reconciliation metadata.

Only a vendor-issued receipt can mark a delivery `SENT`. A timeout, process loss, or
otherwise ambiguous external outcome enters `UNKNOWN`; it is reconciled before any
new vendor request and is never blindly retried. An Obsion delivery identifier is an
internal correlation and idempotency key, never a vendor receipt. Final delivery also
re-evaluates Policy and recipient audience authorization.

### 6. Keep Stream and HTTP as thin Experience adapters

The DingTalk Python Stream SDK is loaded only in the adapter process and is limited to
vendor lifecycle, callback validation, conversion to `TrustedImEvent`, and fast
acknowledgement. The existing signed HTTP callback remains a compatibility ingress
and must call the same acceptance service. Neither adapter directly creates Harness
rows, calls a model, performs a connector invocation, owns delivery state, or makes
authorization decisions. Agents never receive DingTalk application credentials.

## Consequences

- M1a requires a forward Alembic migration for installations, Inbox events, audience
  bindings, immutable identity/fingerprint constraints, worker leases, and any
  backward-compatible links needed by existing principal bindings.
- Stream parsing alone, local fixtures, and synchronous bridge tests do not complete
  M1. The durable Inbox, worker dispatch, audience boundary, four-intent admission,
  and delivery Outbox/`UNKNOWN` reconciliation each need implementation and evidence.
- M1 is distinct from the blocked Phase 98 operator-promotion gate. M1 work cannot
  substitute for staging, UAT, recovery, vulnerability, signing, OIDC/secret-manager,
  security, data-owner, or publication evidence required for promotion.
- No production DingTalk credential, application secret, PAT, or tenant data is
  introduced by this decision. Live tenant testing remains an operator-owned gate.

## Implementation amendment — 2026-09-09

The working-tree migration adds nullable installation attribution to existing sender
bindings, preserves legacy NULL-attribution uniqueness with a partial index, and
uses `(installation_id, sender_id)` uniqueness for trusted bindings. This is an
additive API contract: omitted installation remains legacy; trusted ingress requires
an explicit matching installation. Existing bindings are not silently migrated.

Inbox records pin the accepted subject in immutable metadata. Dispatch revalidates
both the active installation and the same binding/user, so administrative reassignment
cannot transfer queued work. Canonical JSON encoding prevents delimiter collisions
in fingerprints, and redaction happens before fingerprinting and Inbox persistence.
PostgreSQL rejects
identity changes, terminal rewrites, invalid claim generations and inconsistent
installation/sender/audience relationships. Dispatch commits Run creation and Inbox
linkage in one row-locked transaction; no network or model work occurs in that transaction.

Admission also pins one of `QUERY`, `SUMMARY`, `ANALYSIS`, or `CREATE` plus a bounded
classification reason. Unknown text defaults to QUERY. This value is descriptive and
restrictive context only: Policy, grants, ACLs, declared capabilities and side-effect
rules remain the sole authority, so the CREATE label grants no write capability.

The pre-Run Inbox and administrator audience mapping are explicitly reviewed metadata,
not an exception permitting a second Harness event protocol. The existing EventStore
remains the exclusive writer of Harness events and runtime outbox messages.

The DingTalk signed compatibility HTTP adapter now normalizes the same stable event,
installation, corporation, and configured app-key identity as Stream before calling
the Bridge. When an application key is configured, missing identity rejects the
callback rather than falling back to legacy synchronous ingest. This preserves the
Inbox's replay and audience boundary across both vendor transports.
