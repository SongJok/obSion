# Phase 90 per-stage investigation narrative architecture review

## Review question

Can the Runtime timeline present a per-stage investigation narrative —
step duration, produced evidence, supported conclusions — purely as a
projection of persisted rows, without inferred links, generated prose,
or new API surface?

**Status: PASS for persisted-key correlation; PENDING for all six
operator gates.**

## Invariants reviewed

- **Runtime architecture unchanged**: one Python control plane, one App
  Server, one Harness, Workspace → Thread → Turn → Run → Step → Event,
  and Capability Gateway → Policy → connector for every external access.
  This phase touches `runtime-inspector.tsx`, CSS, tests, and docs only.
- **Correlation integrity**: the only join keys are `Evidence.step_id`,
  `Claim.evidence_ids`, and `RunStep` timestamps — all written by the
  control plane at execution time. The UI builds no heuristic
  attribution (no name matching, no proximity guessing).
- **No invented data**: missing timestamps render no duration; steps
  without evidence render no chips; unattributed rows (nullable
  `step_id`, e.g. Run-start document attachments) appear in an explicit
  section rather than being dropped or misassigned.
- **Attribution chain preserved**: chips navigate to the Phase 89 typed
  Evidence detail with its full metadata ledger; the Phase 88 render-
  time selection reset on Run change still prevents cross-Run
  misattribution.
- **Frozen surface respected**: no endpoint, schema, capability, policy,
  or event change; no model call is involved anywhere in the narrative.

## Boundary confirmation

- A generated (model-written) investigation story is explicitly out of
  scope and would require its own gated capability review.
- Post-conclusion context actions, operations analytics loop, full admin
  CRUD, schema-driven chart renderer, and the JS component-test stack
  remain deferred candidates.

## Verification

- `services/control-plane/tests/test_phase90_investigation_narrative.py`
  (7 tests) pins persisted-key correlation, no-fabrication duration,
  bounded chips, claim linking, and the unattributed section.
- Web typecheck, lint, and production build pass.
- `make check`, `make test-java`, and
  `make validate-release-candidate-contract` pass on the final tree.
