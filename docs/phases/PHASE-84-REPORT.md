# PHASE-84-REPORT — Alpha.1 live-tenant evidence ledger

## What was implemented

- Added `docs/release/alpha1-live-evidence-contract.yaml` (`LiveEvidenceLadder`):
  six probes bound to the existing opt-in pytest nodes, each with an evidence
  surface and contract-allowed classifications; the single write probe keeps its
  own `OBSION_FEISHU_SEND_LIVE` + `OBSION_FEISHU_LIVE_CHAT_ID` opt-in.
- Added `obsion.release.live_evidence`: ladder contract loading with node-id
  existence checks, a bounded pytest runner, junit/record classification,
  credential redaction, canonical SHA-256 ledger checksums, and offline ledger
  validation with union coverage across profiles. `write_probe_record` is the
  shared emission helper for control-plane probes; the IM adapter probes emit
  the same record shape.
- Instrumented all six live probes to emit structured results through
  `OBSION_LIVE_PROBE_DIR`. Without that variable the probes behave exactly as
  before.
- Added `obsion record-live-evidence --profile-label <slug>` and
  `make record-feishu-live-evidence`. Both fail closed (exit 2) without
  `OBSION_FEISHU_LIVE=1`, credentials, and a profile label; the send probe runs
  only with `--include-send-probe` and the operator's own send opt-in.
- Extended `docs/release/alpha1-candidate-gates.yaml` with a `liveEvidence`
  section and taught `validate_release_candidate` to validate it in every mode:
  schema, checksum, allowed classifications, no failed probes, no skipped
  required probes, and full ladder coverage across the referenced ledgers. The
  summary reports `live_evidence_ledgers`/`live_evidence_probes`; promotion
  eligibility is untouched.
- Recorded two real ledgers against the operator tenant and documented the new
  env variable in `.env.example`.

## Architecture decisions

ADR 0063 records the core decisions: live evidence is a recorded, redacted,
checksummed ledger rather than prose; a skip is never a pass (post-opt-in skips,
missing records, and disallowed outcomes are `failed`); the recorder enables the
read-only browse opt-in only inside the browse subprocess and never enables the
send opt-in; and recorded live evidence never feeds `promotion_eligible`.

No runtime path changed: the one Python control plane, one App Server, durable
Harness hierarchy, Capability Gateway, Policy, Evidence, and credential
boundaries remain unchanged. The recorder creates no Run, Turn, Event, Evidence,
Approval, or audit row and reuses the production probe tests and clients.

## Migration

No database or Event revision is added. Alembic drift validation remains part of
the phase gate.

## Validation

- `test_phase84_live_tenant_evidence.py` (19 passed) covers the ladder contract,
  probe instrumentation, recorder classification (passed/denied/failed/skipped),
  opt-in and credential gating, record-less and skip failure, disallowed
  outcomes, credential-material rejection, checksum tampering, forbidden keys,
  union coverage, candidate-gate binding, the fail-closed Make target and CLI,
  release notes, and project status.
- Historical Phase 76, 78, 81, 82, and 83 suites continue to pass; the CLI
  default release manifest is now `0.84.0-dev`.
- `make check` covers Ruff formatting/lint, strict mypy, contract/Event/
  evaluation/release validation including the new `liveEvidence` section,
  secret scanning, all Python and frontend tests, and Alembic drift.
- Live runs with operator process credentials (never printed or persisted),
  revision `467fe95`:
  - readonly profile: tenant token `passed`; chat listing, missing document,
    wiki listing, and Gateway browse `denied` with correct vendor
    classifications; send probe `skipped` (not requested).
  - agent profile: tenant token `passed`; chat listing `passed` (one bot
    chat); document/wiki/browse `denied` (scopes ungranted, fail-closed);
    send probe `passed`, vendor message id `om_x100b666298f33ca8c2b188749811eb0`
    delivered once to the Phase 81 ephemeral validation chat.
- `obsion validate-release-candidate --contract-only` reports
  `live_evidence_ledgers: 2`, `live_evidence_probes: 6`,
  `promotion_eligible: false`, and the six pending operator gates.

## Remaining operator gates

- Clean staging/UAT, timed PostgreSQL/object-store restore, registry
  HIGH/CRITICAL CVE policy and signatures, live OIDC/secret manager/read
  replicas, security/data-owner approval, and maintainer-authorized publication
  remain `PENDING`; recorded live evidence is readiness input, not promotion
  authority.
- DingTalk and WeCom tenants, public DNS/TLS ingress, and a permitted live
  Feishu document remain operator-owned; the ladder records correct denial
  rather than fabricating access.
- Ledgers are point-in-time audit records at one revision; refreshing them is
  an explicit operator action via `make record-feishu-live-evidence`.
