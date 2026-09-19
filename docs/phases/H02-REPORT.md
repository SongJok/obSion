# H02 report: task contract, obligations, and semantic revisions

Date: 2026-09-19
Baseline: `dbcf708a82020db6e59672b020e0360f4c49a6d5`
Architecture gate: [versioned TaskContract](../architecture/phase-h02-task-contract-gate.md)
Decision: **implementation and full local validation complete; remote code-candidate validation pending**

## Outcome

Every newly prepared Harness Run now carries a strict, fingerprinted TaskContract in
its existing RunIntent. The contract freezes the goal, source scope, objects, time and
timezone, accuracy, deliverables, constraints, environment boundary, descriptive
authority binding, budgets, success criteria, and task-specific information
obligations before a plan can execute.

The implementation remains inside the one Python control plane. It adds no schema
migration and no second Agent service. Historical RunIntent v1 records without a
contract remain readable.

Exact attachments and selected documents are closed source scopes: neither initial
planning nor model-assisted investigation may broaden them to an organization search.
Attachment/source bodies never enter the Builder and cannot modify filters, scope, or
authority. The contract explicitly says it is descriptive only; runtime Gateway and
Policy checks remain authoritative.

Contextual changes to a metric or time window are parsed from the current turn rather
than from concatenated old prose. A material change increments the contract revision,
pins the parent fingerprint, invalidates the affected plan/query/statistics/evidence
review/claims/artifacts, recompiles from the new semantics, and prevents old answer or
memory text from reaching the author. Unchanged fields still pass through the current
authorized catalog and source-access checks.

One safe contract summary is attached unchanged to the plan, author prompt,
investigation payload, Verify Step, and final Answer Artifact; Artifact lineage pins
the fingerprint. The summary removes raw Principal, organization, role and permission
digests while retaining the fact that Gateway and Policy enforcement are mandatory.

## Requirements traceability

| Requirement | Implementation | Validation |
|---|---|---|
| H02-T01 versioned contract | `domain/task_contract.py`, optional `RunIntent.task_contract`, runtime Builder binding | strict round-trip, fingerprint tamper, legacy intent and public projection tests |
| H02-T02 necessary dimensions | extensible `DimensionRequirementRegistry` with five built-in task families | HARD/OPTIONAL and plugin registration tests |
| H02-T03 investigate before asking | existing authorized `IntentContextExplorer` retained and contract-bound | unique/ambiguous authorized-directory tests |
| H02-T04 authorized follow-up and invalidation | current ACL recheck, semantic amendment parsing, parent/revision/invalidation set, old-history exclusion | API-level time amendment plus unit metric-amendment tests |
| H02-T05 one summary everywhere | summary helper used by runtime, task prompt and investigation; output lineage binding | two-attachment end-to-end summary equality test |

## Validation

- H02 contract and all four acceptance paths: PASS. The focused H02/H01/SQLite
  regression set completed with 32 passing tests.
- CI-equivalent Python quality suite: PASS — 3,020 passed, 285 explicitly skipped,
  80.06% coverage in 490.42 seconds. The separately isolated contract-distribution
  test also passed.
- Ruff, formatting, strict mypy across 291 source files, contract/status/evaluation/
  release validators, secret scan, and repository diff checks: PASS. The secret scan
  reported zero findings.
- JavaScript workspaces: PASS — lint, typecheck, production build, and 297 tests
  across Desktop, IDE, Web, and TypeScript SDK.
- PostgreSQL-only, destructive-migration, and live-vendor tests remained explicitly
  skipped in the local profile; their configured CI jobs and container gates remain
  mandatory for the pushed candidate.
- Remote code-candidate results will be recorded before H02 is marked complete.

The main-branch H01 documentation revalidation exposed a slow-host SQLite duplicate
receipt failure in addition to a static producer-manifest failure. H02 runtime edits
already required refreshing that reviewed line manifest. SQLite's real serialized
writer path can outwait the driver's five-second default; the local/test-only timeout
is now 30 seconds. The 100-receipt/20-processing stress case passed five consecutive
focused runs and the full CI-equivalent suite. Production PostgreSQL settings are
unchanged.

## Migration and rollback

Database migration: **none**. TaskContract is stored in the existing Run intent JSON.
Rollback does not require a database downgrade. Older code can retain historical JSON
but must not reinterpret a contract snapshot as authority.

## Deferred work

- H03: governed resource catalog and relation graph.
- H05: live DimensionState, sufficiency scoring, conflict resolution and stop rules.
- H08: complete statistical obligation DSL, reconciliation, exact rounding and exports.
- H19: independent calibration and protected holdout release gates.
