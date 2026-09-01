# Phase 89 typed Evidence views architecture review

## Review question

Can the Workbench render goal.txt section 57's typed Evidence Panel
(Metric / Log / Deployment / Git Diff / Config Diff and friends) purely
from already-persisted Evidence rows — without inventing payload fields,
adding API surface, or weakening any fail-closed boundary?

**Status: PASS for typed rendering on persisted envelopes; PENDING for
all six operator gates.**

## Invariants reviewed

- **Runtime architecture unchanged**: one Python control plane, one App
  Server, one Harness, Workspace → Thread → Turn → Run → Step → Event,
  and Capability Gateway → Policy → connector for every external access.
  This phase touches `apps/web` rendering, tests, and documentation only.
- **Data source fidelity**: every rendered value comes from the REST
  `EvidenceView` projection of a persisted `Evidence` row. The classifier
  keys (`events[]`, `items[]`, `columns`/`rows`, `plan`/`validation`,
  `hits`, `text`) are exactly the envelopes produced by
  `capabilities/observability.py`, `capabilities/engineering.py`, the
  PostgreSQL read-only executor, the knowledge handler, Code
  Intelligence, and attachment ingestion. `tool-result` and `citation`
  are correctly not treated as Evidence types (they are a model-context
  segment label and a derived answer reference, respectively).
- **No invented data**: all accessors are type guards; unknown or
  dynamically-mapped capability payloads keep the generic JSON fallback.
  Truncation notes state the displayed/total counts instead of silently
  dropping rows.
- **Attribution integrity**: the metadata ledger renders the persisted
  `run_id`/`step_id`/fingerprint/lineage, so Claim → Evidence navigation
  terminates on an auditable record; the Phase 88 cross-Run selection
  reset still guards against misattribution.
- **Session and credential invariants hold**: no new network calls, no
  browser token persistence, no secrets in rendering paths (the control
  plane's redaction and fingerprinting already ran at ingestion).
- **Frozen surface respected**: no endpoint, schema, capability, policy,
  or event change; the `evidence.created.v1` contract and the candidate
  gates are untouched.

## Boundary confirmation

- `EvidenceObservation` remains an internal model with no REST
  projection and is deliberately not visualized here.
- The Phase 88 deferred findings that imply new product surface
  (per-stage investigation narrative, post-conclusion context actions,
  schema-driven chart renderer, full admin CRUD, JS component-test
  stack) stay deferred with their own future reviews.

## Verification

- `services/control-plane/tests/test_phase89_typed_evidence.py`
  (10 tests) pins the classifier keys, type-guard accessors, goal.txt
  section 57 coverage, fallback preservation, metadata ledger, and the
  REST-matching TypeScript type.
- Web typecheck, lint, and production build pass.
- `make check`, `make test-java`, and
  `make validate-release-candidate-contract` pass on the final tree.
