# ADR 0063: Alpha.1 live-tenant evidence is a recorded, redacted, checksummed ledger

- Status: Accepted
- Date: 2026-08-31

## Context

Phases 76–81 proved that the Feishu integration behaves correctly against a real
tenant, but the proof lived in terminal output and phase-report prose. An
open-source release candidate cannot cite "an operator once ran this" as durable
evidence: results must be machine-readable, credential-free, integrity-protected,
and reviewable in CI without re-running vendor traffic. At the same time, recorded
live behavior must never be confused with production-promotion authority, and a
probe that did not run must never be counted as a pass.

## Decision

Live validation becomes a declared ladder plus a recorded ledger.

`docs/release/alpha1-live-evidence-contract.yaml` (`LiveEvidenceLadder`) binds each
probe id to exactly one pytest node, its evidence surface, the classifications the
contract accepts (`passed` and/or `denied`), and, for the single write probe, its
own pre-existing opt-in variables. The ladder reuses the production probe tests;
it adds no second vendor client and no new vendor path.

`obsion record-live-evidence` (module `obsion.release.live_evidence`) runs the
ladder against the operator's tenant. It requires the global read opt-in
`OBSION_FEISHU_LIVE=1` and environment credentials, enables the read-only browse
opt-in for the browse subprocess only, and never enables
`OBSION_FEISHU_SEND_LIVE` itself: the write probe runs solely when the operator
sets that variable and passes `--include-send-probe`. Each probe test emits a
small structured record through `OBSION_LIVE_PROBE_DIR`; the recorder classifies
the junit outcome and the record together. A junit failure is `failed`, a skip
after opt-in is `failed` (a skip is never a pass), a passing test without a
result record is `failed`, and an outcome outside the contract-allowed set is
`failed`. The only legitimate `skipped` entry is an optional probe the operator
did not request.

The ledger (`LiveEvidenceLedger`) stores per-probe classification, a bounded
content-free detail, timestamps, the profile label, a truncated SHA-256
fingerprint of the app id, and the recording git revision. Raw app ids, app
secrets, tenant tokens, bearer material, and forbidden keys are rejected both at
record time and at validation time. A SHA-256 checksum over the canonical ledger
payload makes later edits detectable. Ledgers live under
`docs/release/evidence/alpha1/`, the same directory the candidate gate already
reserves for operator evidence.

`validate-release-candidate` validates the new `liveEvidence` contract section in
every mode: schema, checksum, per-probe allowed classifications, absence of
`failed` entries, no skipped required probes, and union coverage of every ladder
probe across the referenced ledgers. The result is reported as
`live_evidence_ledgers`/`live_evidence_probes` and never feeds
`promotion_eligible`: live-tenant behavior evidence and the six external
promotion gates remain separate claims.

## Consequences

- Feishu tenant authentication, chat discovery, document/wiki fail-closed denial,
  Capability-Gateway browse classification, and single-message delivery are now
  durable, reviewable repository evidence instead of prose recollection.
- Re-running the ladder requires the same explicit operator opt-ins as before;
  CI validates recorded ledgers offline and performs no vendor traffic.
- DingTalk, WeCom, public TLS ingress, and permitted Feishu document content
  remain operator-owned; the ladder records correct denial, not fabricated
  access.
- Phase 84 adds no Harness, Event, database, production-write, or
  second-control-plane surface.
