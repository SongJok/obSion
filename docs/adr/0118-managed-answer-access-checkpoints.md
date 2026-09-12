# ADR 0118 — Managed answer access checkpoints

- Status: accepted as an incremental development control; historical access and delivery fencing remain pending.
- Date: 2026-09-12
- Builds on: ADR 0115–0117 and the WITHHOLD publication contract.

## Decision

Retrieval permission is not sufficient for a long model call. Before authoring, before independent review and after review, the Harness rechecks managed document references against current local identity, Policy, document ACL, current version and the mandatory source-access predicate. It identifies managed documents by stored document type as well as the evidence source label. Malformed managed references, missing documents, version mismatch, expired access and revoked Policy withhold the answer. The checks perform local database reads only; no external capability or credential bypass is introduced.

Each managed check records the Policy decision and a content-free authorization audit. Already denied content never reaches the author; revocation during authoring prevents the reviewer call; revocation during review prevents the final answer prose and citations from entering answer artifacts, reports or `answer.delta`. Access denial uses the existing extensible Critic conflict representation and WITHHOLD behavior. Existing strict event check schemas are preserved. The visible response explains that source permission or version changed and asks the user to retry after revalidation.

Document retrieval refreshes ORM state so a previously loaded document cannot retain an obsolete ACL during a later checkpoint in the same session. The immutable evidence and rejected claims remain available for audit under existing routes; those routes need the follow-on historical-access control before normal rollout.

## Compatibility and migration

No schema migration, event version change or external connector activation. Ordinary documents and evidence-free everyday questions preserve their behavior. Reuse source migration `e1f3a6b8c5d7`. The managed source runtime must not be activated in the ordinary development deployment until the remaining access boundaries are implemented and verified.

## Validation and remaining boundaries

Synthetic Harness tests exercise allowed publication, revocation before authoring/during authoring/during review, lease expiry, Policy denial, version mismatch, a missing source label and an ACL change outside the ORM identity map. Assertions cover model-call suppression, the final answer, citations, report markdown, final answer events and Policy-linked denial audit. Real DingTalk/Kimi calls are not part of these tests.

These are access checkpoints, not an atomic distributed revocation barrier. A permission change after the final checkpoint can still race with transaction commit or later IM delivery. Historical artifacts, claims, evidence, event replay, conversation/memory snapshots and copied/replayed Runs require source lineage enforcement; the IM outbox must recheck before sending. The current artifact and workspace access paths cannot be treated as sufficient for that purpose. Complete those boundaries, add PostgreSQL concurrent revocation tests, then perform the normal-runtime and pointbot document acceptance. This ADR does not claim that work complete.
