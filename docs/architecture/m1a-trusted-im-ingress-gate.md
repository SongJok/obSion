# M1a trusted IM ingress architecture gate

## Review question

Can a vendor-authenticated DingTalk event be acknowledged promptly by its transport
after durable, installation-scoped control-plane acceptance, then create at most one
user-owned Harness task without exposing group-restricted content or retrying an
uncertain external effect?

**Status: PASSED for the repository-local M1a control-plane scope.**
Installation-scoped admission, durable Inbox, asynchronous Run dispatch,
subject-pinned bindings, PostgreSQL mutation guards and content-free status lookup
are implemented in the working tree. Real PostgreSQL concurrency and migration
checks pass. M1b delivery is completed for repository-local scope; M1c
transport/operator gates remain open. No live DingTalk or production approval is
represented here.

## Scope and sequencing

M1 is the formal DingTalk product-entry milestone from `second_goal.txt`. Its full
acceptance requires Stream ingress, installation/person/project bindings, four-intent
admission, Inbox/Outbox reliability, group audience control, and real test-tenant
evidence. It is delivered in three bounded increments:

| Increment | Deliverable | Completion evidence |
|---|---|---|
| M1a | Trusted installation mapping, immutable PostgreSQL Inbox, idempotent acceptance, asynchronous user-owned Harness dispatch, and administrator-managed audience bindings | Forward/backward migration proof; duplicate/restart/lease tests; no task from untrusted input; group default-deny tests |
| M1b | Durable extension of `ImDelivery` as an Outbox with leases, backoff, rate limits, actual receipts, and `UNKNOWN` reconciliation | **Repository-local passed**: [M1b gate](m1b-durable-im-delivery-gate.md) and delivery report |
| M1c | DingTalk Stream lifecycle wired to the shared acceptance endpoint, test-tenant exercise, and operator runbook evidence | **Repository-local HTTP compatibility passed**: signed callbacks with configured app identity normalize into the same trusted Inbox path. Redacted test-tenant question and allowlisted creation flow; staged receipt/replay/denial evidence; measured acknowledgement and first-status timing remain operator evidence. |

M1 is incomplete until all three increments pass their automated and operator gates.
Phase 98 remains the active blocked operator-promotion phase. Its pending evidence
cannot be replaced by M1 implementation, and M1 work cannot claim production
readiness or advance the official phase status.

## Required M1a contract

```text
vendor-authenticated normalized event
  -> control-plane trusted-input admission
  -> PostgreSQL installation + Inbox transaction + Audit commit
  -> fast vendor ACK
  -> Python Inbox worker lease/fencing
  -> existing principal resolution + Workspace/Thread/Turn/Run services
  -> Harness Event and Web task projection
```

The adapter is an Experience boundary. It may validate and normalize a vendor event,
but cannot make authorization decisions, create a Run, call a model, invoke a
Capability, or send a response based on terminal Run state. Model and connector work
continues through the existing Harness, Model Gateway, Capability Gateway, Policy
Engine, and Audit paths.

### Installation and identity

- Persist a unique, active DingTalk `corp/app -> organization` installation mapping.
  The mapping must be created, changed, disabled, and audited by an authorized
  administrator.
- Require installation identity, vendor event ID, stable sender ID, conversation ID,
  and bounded message content at trusted-input admission.
- Resolve the sender only through an active principal binding belonging to the mapped
  organization. Nicknames and display labels cannot select a Principal.
- Preserve legacy records in a forward migration. A legacy DingTalk binding without
  an installation is pending remediation and cannot be attributed by inference.
- Store no application secret, access token, or raw credential in an installation,
  Inbox row, API response, Audit metadata, Event payload, or log.

### Inbox idempotency and dispatch

- Persist and commit a sanitized, versioned Inbox row before acknowledging the vendor.
- Enforce a database unique key on `(installation_id, vendor_event_id)` and bind it
  to a canonical SHA-256 payload fingerprint.
- Return the original accepted outcome for an exact replay. Reject same key and
  different fingerprint as a conflict; neither concurrent submission nor process
  restart can create a second logical Thread, Turn, or Run.
- Claim Inbox work in a same-control-plane Python worker using a lease, heartbeat,
  fencing token, bounded retry classification, and terminal immutable linkage to the
  resulting Harness records.
- Acknowledgement must not wait for model inference, Run completion, outbound
  delivery, or a vendor receipt. The user-facing status and Web link derive from the
  durable task after dispatch.

### Intent and group audience

- Record one of query, summary, analysis, or create at admission, with the decision
  and a reason auditable. Intent cannot grant a capability or expand an ACL.
- Map groups and projects through administrator-managed installation-scoped bindings.
  Private per-user context is the default for a shared conversation.
- Re-authorize every resource access for the user and project. A source visible to a
  user is not automatically publishable to a group.
- Require an explicit recipient-audience decision before a final group response. A
  denied or uncertain decision produces only content-free status plus a separately
  authorized Web link.

The M1a implementation keeps all trusted group replies status-only. Delivery reads
the group fact from the immutable Inbox, revalidates installation/sender/user lineage,
and ignores a less restrictive context hint. Content-bearing group authorization is
therefore deferred rather than inferred from a project mapping.

## M1b delivery evolution

M1b retains `ImDelivery` as the delivery fact. It adds durable Outbox claiming,
scheduled retry metadata, bounded backoff, rate-limit integration, reconciliation
information, recipient-audience snapshots, and an `UNKNOWN` terminal or held state.
Only a vendor-issued `messageId` or `message_id` completes a DingTalk delivery. A
local delivery UUID cannot be substituted for a receipt, and an ambiguous external
outcome cannot be resent until reconciliation establishes that doing so is safe.
The repository-local implementation is documented by the dedicated M1b gate; live
vendor receipt and reconciliation evidence remain part of M1c.

## Automated acceptance map

M1a must add focused tests that prove all of the following against PostgreSQL where a
database constraint or lease is claimed:

- an active installation maps to exactly one organization, and an unrecognized,
  disabled, cross-organization, or legacy-unattributed installation is rejected before
  a task is created;
- 100 concurrent submissions of one installation/event/fingerprint create one Inbox
  row and at most one logical Harness task;
- exact replay after process restart returns the original accepted identity, while a
  same event ID with another fingerprint is a stable conflict with no second task;
- the ACK is available after the Inbox commit without waiting for a Run, and a worker
  crash/reclaim path preserves the original task correlation while a fenced worker
  cannot publish twice;
- four-intent classification narrows behavior and cannot bypass Policy, connector
  grants, project ACL, budget, or existing side-effect controls;
- group/project mappings default to deny, user-private context does not leak a final
  answer into an unauthorized group, and the safe status/link fallback contains no
  restricted answer content;
- Audit and Harness/Event links identify installation, bound Principal, Inbox event,
  task, Policy decision, and delivery attempt without persisting credentials or raw
  vendor response bodies;
- architecture tests ensure trusted adapters do not import the control-plane database,
  call a model, or bypass Gateway/Policy. M1c will require both Stream and HTTP
  adapters to call this shared admission path.

The working-tree M1c compatibility tests now prove the signed DingTalk HTTP route
preserves stable event, installation, corporation and app identity before Bridge
admission, makes one `trusted-events` request without waiting for a Run, and rejects
incomplete trusted identity rather than falling back to legacy synchronous ingest.
This closes only the repository-local HTTP convergence portion of M1c.

M1b proves durable delivery lease recovery, exact receipt handling,
bounded retry of known pre-send failure, no retry of `UNKNOWN`, recipient recheck at
send time, and operator reconciliation before a new vendor request.

## Migration and rollback review

M1a requires an additive Alembic revision. It must retain historical principal and
delivery rows, introduce new tables and foreign keys before activating the new ingress,
and pass upgrade, downgrade, re-upgrade, and autogenerate-drift checks in an isolated
PostgreSQL database. Database guards must protect tenant identity, Inbox fingerprint
immutability, one-way state transitions, worker fencing, and terminal task linkage.

Rollback disables new Stream/HTTP task acceptance before reversing application code.
Already accepted Inbox rows remain inspectable and are either safely dispatched,
cancelled, or reconciled; rollback must not discard pending user input or claim that an
external delivery completed.

## Pending operator evidence

- Provision a non-production DingTalk application and store its credentials only in
  the approved secret manager; do not commit, log, or paste them into this repository.
- Configure an authorized installation, user bindings, and an allowlisted group/project
  audience in a test organization. Demonstrate allow, deny, revocation, and group-safe
  fallback behavior.
- Exercise vendor redelivery, a deliberately interrupted worker, a receipt-loss
  scenario, cancellation, and a long-running progress update. Attach redacted
  timestamps, receipt/reconciliation outcomes, Audit correlations, and task links.
- Measure platform acknowledgement p95 at or below one second, first status p95 at or
  below three seconds, and long-task progress or liveness at least every 15 seconds.
- Collect staged security, data-owner, tenant administrator, recovery, and release
  approvals. Local mocks and automated checks do not satisfy these human gates.
