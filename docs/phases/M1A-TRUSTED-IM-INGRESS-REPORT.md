# M1a trusted IM ingress implementation report

Status: COMPLETE for the repository-local M1a control-plane increment. Updated
2026-09-09. This is a working-tree validation record, not a release, live Stream,
M1 completion, or production-readiness claim. The official completed baseline
remains Phase 97; Phase 98 promotion is blocked.

## Implemented

- Administrator-managed `ImInstallation` maps channel/corp/app to one organization.
  Sender bindings now explicitly reference that installation. Historical bindings
  keep a NULL installation and remain available to the legacy API; trusted ingress
  never infers an installation from them. The same stable sender can be bound
  independently in different installations.
- `POST /api/v1/experience/im/trusted-events` commits Inbox acceptance and Audit
  before returning 202. The adapter does not wait for a Harness Run on this path.
  Exact `(installation_id, vendor_event_id)` replay returns the original record;
  changed canonical JSON fingerprint conflicts. Sensitive text is redacted before
  both fingerprinting and Inbox persistence, so low-entropy credentials do not leave
  guessable derived fingerprints. No raw callback body or application credential is
  retained.
- Admission pins exactly one `QUERY`, `SUMMARY`, `ANALYSIS`, or `CREATE` intent and
  its bounded reason in the immutable Inbox/Audit record. The label is passed to
  the Run's Understanding snapshot as `admission_intent` and does not add a
  permission, connector grant, resource ACL,
  risk budget, or side-effect authorization. Unrecognized text defaults to QUERY.
- `GET /api/v1/experience/im/trusted-events/{event_id}` exposes content-free status
  and eventual Run identity to the subject or an authorized IM delegate. The Python
  SDK supports acceptance, status retrieval, and installation-scoped sender binding.
- The same Python control plane runs the Inbox dispatcher. Claims use row locks,
  leases and monotonically increasing attempt generations. Dispatch, rejection and
  release check ownership, generation and expiry, including same-process reclaim.
  Run creation and immutable Inbox linkage commit in one transaction. Recovery
  transaction failures do not terminate the worker loop; repeated failures stop at
  the configured attempt budget (default 5) with a durable rejection Audit. Dispatch
  rechecks installation activation, binding activation and the accepted user identity;
  rebinding a sender cannot transfer an already queued task to another user.
- Group conversation context is private to each bound user. Group/project mappings
  are explicit administrator records. Delivery derives group status from the linked
  Inbox record and rechecks the active installation, sender, user and organization;
  a modified Run context cannot turn a group response into answer content. Trusted
  group delivery remains status-only and does not assert that group members can see
  the private answer.
- PostgreSQL guards protect acceptance identity/content/fingerprint, terminal outcomes,
  claim generations, installation/sender/subject consistency and audience attribution.
  The normal Harness Event/Outbox remains the only runtime trajectory; Inbox is a
  pre-Run admission ledger, not a parallel event protocol.

## Validation observed

- `test_postgres_im_inbox.py`: 100 concurrent identical admissions produce one Inbox
  and one initial acceptance. Recreating the engine/service replays the same identity;
  repeated dispatch creates one actual Harness Run. Attempts to mutate accepted text,
  fingerprint or terminal linkage fail in PostgreSQL.
- `test_postgres_im_inbox_migration.py`: upgrade, downgrade, re-upgrade and Alembic
  drift checks pass in an isolated PostgreSQL 17 database. Historical sender binding
  identity and activation survive; installation remains NULL after upgrade.
- Local recovery tests cover exact replay/mismatch, stale-worker dispatch/reject/release,
  installation revocation, cross-installation denial, sender reassignment, credential
  redaction, four-intent audit, cross-organization denial, persisted group audience,
  pre-delivery revocation, and subject-scoped content-free status.
- Architecture error-producer and single-event-protocol manifests explicitly register
  the new reviewed surfaces. Runtime event ownership checks remain intact. OpenAPI is
  regenerated and Python strict typing passes. Full-suite totals are tracked in
  `docs/project-status.yaml`. Latest full check: 1097 passed, 29 conditional skips;
  latest focused recovery and architecture check: 29 passed. Frontend workspace tests pass (181 Web,
  17 desktop, 12 IDE, 24 TypeScript SDK). Ruff formatting/lint, strict Python and
  TypeScript checking, release contracts and credential scan pass (0 findings).

Tests use local fixtures, an explicitly disposable PostgreSQL container and existing
application services. A separate DWS read-only check found the specified bot application published and
its robot configuration ONLINE in STREAM mode. This is configuration evidence only.
None of these results proves a real DingTalk message connection,
public ingress timing, outbound delivery, or a live Yunxiao PAT scope.

## Migration and rollback

Revision `c9d1e4f6a2b3` follows `b88f1c4d5e60`. It adds installation, audience and
Inbox tables, nullable installation attribution on existing sender bindings, and
Delivery reconciliation fields/UNKNOWN status. Composite foreign keys preserve
organization ownership. Legacy sender uniqueness is retained by a partial index;
installed senders have unique `(installation_id, sender_id)` identity.

Rollback first stops trusted admission and the Inbox worker. A destructive downgrade
is only tested against disposable databases. A production rollback normally disables
this feature and preserves the ledger. An old-schema downgrade cannot represent
multiple installed bindings for one legacy sender or UNKNOWN deliveries: it fails
rather than silently merging identities or inventing a delivery result. Reconcile and
retain these records before any operator-approved destructive downgrade.

## M1 follow-on work

M1a ends at the shared trusted-event control-plane boundary. M1b now implements
durable delivery leasing, bounded backoff and UNKNOWN reconciliation; see
`M1B-DURABLE-IM-DELIVERY-REPORT.md`. M1c now has repository-local compatibility HTTP
routing through that same admission endpoint; complete Stream lifecycle, actual
tenant receipts, and measured acknowledgement/first-response latency remain operator
evidence. Do not count normalized fixtures as a working live bot.

The working tree now also exercises the M1c repository-local compatibility boundary:
a signed DingTalk HTTP callback with a configured application key preserves the same
event, installation, corporation and application identity as Stream and enters the
trusted Inbox path without waiting for a Run. It rejects incomplete trusted identity
instead of silently using the legacy message path. Live Stream lifecycle, tenant
delivery and timing evidence remain pending operator validation.

M2–M6 from `second_goal.txt`, including actual isolated execution, autonomous project
handling, governed continuous learning and production/UAT evidence, remain separate
unfulfilled requirements. Yunxiao currently implements bounded read-only repository
discovery; file/commit usage and real PAT-visible repository validation remain open.
