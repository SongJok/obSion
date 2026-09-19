# H02 architecture gate: versioned TaskContract

Date: 2026-09-19
Decision: **PASS**
ADR: [0144](../adr/0144-versioned-task-contract.md)
Validated candidate: `2a8d4e621f33e9de3347650b3e7592d9f3348be4`

## Boundary reviewed

H02 extends the existing typed `RunIntent` and single Python Harness. It adds no
service, backend language, database, model loop, connector shortcut, or authority
path. PostgreSQL remains the durable Run truth; Capability Gateway and Policy Engine
remain the only external execution and authorization boundaries.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| New Runs bind a strict versioned contract before planning | PASS | `TaskContractBuilder` creates a content-addressed v1 contract after authorized context exploration; malformed/tampered fingerprints fail validation. |
| Historical RunIntent remains readable | PASS | `task_contract` is optional under RunIntent schema v1; legacy parse/projection tests remain green. |
| Contract cannot grant authority | PASS | Authority contains only principal/organization binding and role/permission digests, is marked descriptive-only, and Gateway/Policy remain required. The safe summary omits raw bindings. |
| Source text cannot change scope/filter/authority | PASS | Builder accepts trusted Turn metadata and catalog results, never Evidence or attachment bodies; source text is absent from the contract and tampering breaks its fingerprint. |
| Exact attachments/documents prevent organization search | PASS | Source-scope validator forbids the combination; Planner and model-assisted investigation both remove organization search. |
| Necessary information obligations are task-specific | PASS | Built-ins cover knowledge, localization, statistics, incident and code with HARD/OPTIONAL requirements; an in-process plugin registry is tested. |
| Ambiguity is resolved before asking | PASS | Existing authorized-context explorer selects one highest-confidence authorized entity and suspends only zero/multiple material candidates. |
| Metric/time amendments invalidate old semantics | PASS | Contract revision/parent and invalidation set cover query/statistics/review/claims/artifacts; runtime recompiles and excludes old conversation/memory semantics. |
| Every reasoning/output stage sees one summary | PASS | Plan, author prompt, investigation payload, Verify Step and Answer Artifact use `task_contract_summary`; output lineage pins its fingerprint. |
| Credentials and hidden chain-of-thought remain absent | PASS | No source content, credentials, raw permission list or hidden reasoning is persisted in the contract. |
| Database migration | NONE | Contract uses the existing `Run.intent` JSON column. |

The CI-equivalent local suite completed with 3,020 passing tests, 285 explicit
environment skips and 80.06% coverage; the isolated contract-distribution test,
strict type/format checks, all repository validators, secret scan, and all JavaScript
workspace checks also passed. GitHub Actions run
[35443823582](https://github.com/SongJok/obSion/actions/runs/35443823582)
then passed all 15 jobs, including PostgreSQL, migration, release-image security and
container smoke gates. Live-provider actions remain explicitly outside H02 and were
not represented as executed.

## Acceptance scenarios

| Scenario | Result | Receipt |
|---|---|---|
| H02-A01 change only time window | PASS | API-level semantic follow-up test proves revision increment, new query parameters, full semantic invalidation and empty old-history dependency list. |
| H02-A02 source asks to ignore filters or raise authority | PASS | Malicious attachment text is persisted only as untrusted Evidence; contract retains the user filter, exact scope and descriptive authority. |
| H02-A03 exactly two attachments | PASS | End-to-end Run ingests both attachments, creates no search Step, and binds the identical summary to intent/plan/verify/output. |
| H02-A04 ambiguous vs uniquely resolvable entity | PASS | Explorer test proceeds for one authorized directory match and emits a bounded clarification for two equal candidates. |

## Migration and rollback

No schema migration is required. Rollback removes contract creation for future Runs
without changing stored JSON or historical records. Existing contracts remain
content-addressed evidence and must never be interpreted as grants by an older runtime.

## Follow-on

H03 may use the contract's exact scope and objects to build a governed resource graph.
H05 owns live dimension state and sufficiency transitions. H08 owns the complete
statistical obligation DSL and reconciliation engine.
