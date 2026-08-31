# Phase 84 Alpha.1 live-tenant evidence architecture review

## Review question

Can live Feishu tenant behavior become durable, credential-free, machine-validated
candidate evidence without adding a second vendor path, weakening probe opt-ins, or
being mistaken for production-promotion authority?

**Status: PASS for recorded live-tenant evidence; PENDING for external promotion.**

## Invariants reviewed

- The runtime architecture is unchanged: one Python control plane, one App Server,
  one Harness, Workspace → Thread → Turn → Run → Step → Event, and Capability
  Gateway → Policy → connector for every external access. The recorder is release
  tooling; it creates no Run, Turn, Event, Evidence, Approval, or audit row.
- The ladder reuses the six existing opt-in probe tests and their production
  clients (`FeishuClient`, `FeishuHttpChannel`, `FeishuDocsClient`, and the
  REST-to-Capability-Gateway browse route). No new vendor endpoint, credential
  flow, or transport is introduced.
- Opt-in semantics are preserved: read probes require `OBSION_FEISHU_LIVE=1`
  (the recorder enables the read-only browse flag only inside the browse
  subprocess), and the write probe still requires the operator's own
  `OBSION_FEISHU_SEND_LIVE=1` plus an explicit `OBSION_FEISHU_LIVE_CHAT_ID`.
- Fail-closed classification: junit failure, post-opt-in skip, missing probe
  record, malformed record, or a contract-disallowed outcome are all `failed`;
  only an unrequested optional probe may be `skipped`.
- Ledgers are content-free and credential-free: truncated app-id fingerprint,
  classifications, bounded details, timestamps, revision, and a canonical
  SHA-256 checksum. Credential-shaped values and forbidden keys are rejected at
  record time and at validation time.
- Candidate-gate binding is promotion-neutral: `liveEvidence` validation reports
  ledger and probe counts and never influences `promotion_eligible`.

## Recorded live results (2026-08-31, revision 467fe95)

Two operator profiles were recorded against the real tenant with process-only
credentials (never printed or persisted):

- `feishu-readonly-live.yaml`: tenant token `passed`; chat listing, missing
  document, wiki space list, and Gateway browse all `denied` with the correct
  vendor classifications; send probe `skipped` (not requested).
- `feishu-agent-live.yaml`: tenant token `passed`; chat listing `passed`
  (one bot chat); document/wiki/Gateway browse `denied` (scopes not granted,
  classified fail-closed); send probe `passed` with vendor message id
  `om_x100b666298f33ca8c2b188749811eb0` delivered to the Phase 81 ephemeral
  validation chat.

Union coverage satisfies all six ladder probes; both ledgers validate offline
through `validate-release-candidate --contract-only`.

## CI acceptance map

- Quality continues to validate the candidate contract, now including the
  `liveEvidence` section, alongside contracts, evaluations, release notes,
  datasets, secret scan, and all test suites; no CI job performs vendor traffic.
- `make record-feishu-live-evidence` is operator-only and fails closed with
  exit 2 unless the opt-in, credentials, and `OBSION_LIVE_PROFILE` are present.
- Secret scanning covers the ledger directory; recorded details carry no tenant
  content, identifiers, or credential material.

## Migration and rollback

There is no Alembic or Event migration. Rollback is reverting the Phase 84
commits and deleting the two ledger files; the candidate contract then must
drop its `liveEvidence` section as well, since an uncovered or missing ledger
fails closed. Runtime data is untouched.

## External gate

The six promotion prerequisites (staging, timed restore, registry CVE/signature,
live identity/secret/replicas, human sign-off, signed publication) remain
`PENDING`. Recorded live-tenant evidence informs readiness review; it is not
staging, UAT, or approval, and Phase 85 promotion work still requires real
external evidence under explicit authority.
