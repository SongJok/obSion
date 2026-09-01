# ADR 0068: Typed Evidence views on persisted envelopes

## Status

Accepted (Phase 89, 0.89.0-dev)

## Context

goal.txt section 57 makes the Evidence Panel a defining product behavior:
opening a conclusion must show typed Metric, Log, Deployment, Git Diff,
and Config Diff evidence. The control plane has persisted exactly those
rows since the Capability Gateway phases — one metadata envelope
(`evidence_type`, `source`, `resource`, `observed_at`, `ingested_at`,
`confidence`, `classification`, `permissions`, `content_fingerprint`,
`lineage`, `step_id`) plus a normalized JSON `content` payload. The
normalized payload shapes are stable and few: observability operations
produce `events[]`, engineering operations produce `items[]`, data
queries produce `columns`/`rows`, `sql.explain` produces
`plan`/`validation`, knowledge retrieval produces `hits`, and document
attachments produce `title`/`text`. Yet both Evidence surfaces (Runtime
inspector, workspace Evidence page) rendered every row as raw JSON, and
the Web `Evidence` type had drifted from the REST projection
(`run_id` optional, `step_id`/`ingested_at` missing).

Two constraints shaped the decision: Evidence `content` has no static
per-type schema union (capability output schemas are versioned JSON, and
`evidence_mapping` is configurable), and the Alpha.1 surface is frozen —
no API or schema change may ride along with a rendering phase.

## Decision

1. **Dispatch on the persisted envelope, never on hope.** The classifier
   (`lib/typed-evidence.ts`) switches on `evidence_type` plus the actual
   presence of `events[]`, `items[]`, `columns`/`rows`,
   `plan`/`validation`, `hits`, or `text` — the same envelopes the
   control plane normalizes today. `git.diff` and `config.diff` are
   operations inside `items[]`, not invented top-level types.
2. **Every accessor is a type guard.** No renderer fabricates a missing
   field, synthesizes a default value, or promotes a nested `kind` to a
   top-level type. Payloads no classifier recognizes keep the raw JSON
   fallback so no evidence row is ever hidden or misrepresented.
3. **The metadata ledger renders only persisted fields.** `EvidenceMeta`
   shows `observed_at`, `ingested_at`, confidence, classification,
   permissions, fingerprint, `step_id`, `run_id`, and lineage straight
   from the row, completing the Claim → Evidence → attribution chain
   that goal.txt's fifth rule (Claim + Evidence + Confidence + Source +
   Timestamp) demands.
4. **Display bounds sit on top of server budgets.** The UI caps tables
   at 100 rows, lists at 200 entries, and attribute chips at 12, with an
   explicit truncation note; it never widens what the API returns.
5. **Keep the established verification pattern.** The contract is pinned
   by `test_phase89_typed_evidence.py` static boundary tests plus the
   existing typecheck/lint/build gate; a JavaScript component-test stack
   remains a separate deferred decision (ADR 0067).

## Consequences

- The goal.txt section 57 Evidence Panel is now real on persisted data:
  METRIC/LOG/TRACE/`deployment.list` render as observability event
  streams, GIT/CONFIG/`deployment.commit`/`k8s.status` as change items
  with diff specialization, DATA as tables and explain plans, CODE as
  symbol locations, and knowledge hits keep their citation provenance.
- The `Evidence` TypeScript type now matches the REST projection exactly,
  removing the impossible "no Run" state.
- A future capability with a novel envelope degrades gracefully to raw
  JSON until a renderer is added — fail-visible, never fail-silent.
- No endpoint, schema, event, or configuration changed; the six PENDING
  operator gates and promotion eligibility are untouched.
