# H01 report: freeze the factual baseline and remove current engineering blockers

Date: 2026-09-19
Repository baseline reviewed: `24d5503826d1901fe95abe8cb80ebb8466949682`
Runtime candidate: `2765f5d2fc06184625b9088c437fc5afd5cca108`
Architecture gate: [comparable candidate baseline](../architecture/phase-h01-comparable-candidate-baseline-gate.md)
Decision: **H01 complete; protected real-business cases NOT_RUN; promotion BLOCKED**

Normative requirements: [product plan](../product/autonomous-harness-plan-20260919.md),
[20-package backlog](../product/autonomous-harness-backlog-20260919.json), and
[attachment provenance](../product/autonomous-harness-requirements-provenance.yaml).

## Outcome

H01 now produces a comparable candidate rather than a version-label assertion. Acceptance Profile v2
binds a full commit, API/Worker package snapshot, image, evaluation policy and all execution-relevant
management configuration. The deployed development API reports its observed package and the connector
catalog has a stable, content-free configuration digest. Any mismatch blocks scoring.

The old 24-case record remains byte-for-byte the historical source: 5 PASS, 1 FAIL, 18 BLOCKED.
Its H01 ledger is a reason-level projection, not a rerun. Three protected vertical case contracts exist,
but no real business questions, gold data, credentials or sandbox attestation were supplied, so all
three executions are honestly `NOT_RUN` and no quality improvement is claimed.

The JavaScript quality failure was reproduced in a clean archive: Desktop and IDE lint resolved
`@obsion/sdk` through an ignored, absent `packages/sdk-ts/dist`. Root lint/typecheck/test/build now
build the SDK first. The same clean archive completed all four commands, including 297 JavaScript
tests. A separate local failure in independent scoring was traced to pytest loading the developer's
`.env` model allowlist; test Settings now disable the repository env file and explicitly clear that
allowlist.

The first code candidate then exposed two additional full-suite contract failures that focused
tests had not covered: the generated OpenAPI document did not include the new snapshot endpoint,
and the reviewed error-forwarding manifest retained an old `main.py` line. Both generated/reviewed
contracts were updated. Test bootstrap now disables repository `.env` loading for every direct
`Settings` construction, not only the shared API fixture. The replacement candidate passed the
complete local Python suite with 3010 passed, 285 explicitly skipped and no failures.

That candidate's remote release job then built and validated both release artifacts successfully,
but Trivy rejected the first runtime image. Exact local reproduction found 102 High/Critical findings
in the stale Python/Bookworm layer; the not-yet-scanned Web image independently contained 56 fixed
findings, mostly from package managers that are unnecessary at runtime. The final candidate moves
both runtimes to exact digest-pinned Alpine 3.24 images, advances Python to 3.12.14 and Node to
22.23.2, and removes npm/Corepack/Yarn from the standalone Web runtime. Trivy 0.70.0 now reports zero
High/Critical vulnerabilities and zero secrets for each final image without ignoring unfixed issues.
The complete Python suite on 3.12.14 passes 3011 tests with 285 explicit skips and no failures.
GitHub Actions run 35439987679 then completed all 15 jobs successfully against the exact runtime
candidate. Its quality job completed the full Python and JavaScript gates; its dependent container
job rebuilt and validated both release artifacts and passed both unchanged Trivy image scans.

## Requirements traceability

| Requirement | Code/contract | Runtime entry | Test | Receipt |
|---|---|---|---|---|
| H01-T01 freeze candidate and execution identity | `evaluations/acceptance.py`, `harness/execution_identity.py`, admin runtime/config snapshots | `acceptance freeze`, `GET /api/v1/admin/runtime-identity`, connector snapshot | productization acceptance, execution identity, H01 baseline tests | [candidate ledger](../release/evidence/harness/20260919-h01-candidate-baseline.json) |
| H01-T02 repair the actual quality blocker | root `package.json` pre-scripts build `@obsion/sdk` | root lint/typecheck/test/build | clean `git archive` + remote quality workflow | GitHub Actions run recorded in the candidate ledger |
| H01-T03 preserve and classify the 24 cases | `evaluations/diagnostics.py` | acceptance report finalization | source SHA, denominator, taxonomy and unknown tests | [diagnostic ledger](../release/evidence/harness/20260919-h01-legacy-diagnostics.json) |
| H01-T04 align release scope | `README.md`, `README_CN.md`, project status | repository documentation | project-status/release validators | `0.98.0-dev`; P1/Phase 99 remain blocked |
| H01-T05 define first protected vertical cases | `h01-vertical-slices-v1.json` | normal App Server → Harness → Gateway/Policy → Evidence/Artifact paths | holdout-content and absence-policy contract test | all three case executions `NOT_RUN`, never Mock PASS |

## Candidate freeze

The local development candidate uses one Python control plane and the existing PostgreSQL database.
The API became healthy after the current migration head was checked; no schema migration was added.
The public ledger contains only hashes, counts and status:

- candidate commit `2765f5d2fc06184625b9088c437fc5afd5cca108`;
- candidate tree and local image digest in the candidate ledger;
- API and expected Worker package: 372 Python/JSON files, exact SHA256 in the ledger;
- eight configuration snapshots: Agents, Capabilities, Connector configuration, Model Endpoints,
  Model Profiles, Policies, Prompts and Skills;
- evaluation policy digest and Profile schema v2;
- `signature_verified=false` and per-Run Worker observation still required.

No connector endpoint, connector configuration, credential reference, enterprise source text, model
identifier or protected gold answer is present in repository evidence.

## Acceptance scenarios

| Scenario | Result | Notes |
|---|---|---|
| H01-A01 candidate/config drift is incomparable | PASS | Candidate reuse, API/Worker package drift and configuration drift fail before scoring; unchanged connector reads remain stable. |
| H01-A02 only the complete quality job counts | PASS | The exact runtime candidate completed all 15 GitHub Actions jobs successfully; quality and its dependent release/container job both completed, with neither accepted through a skipped downstream step. |
| H01-A03 old 24-case record remains 5/1/18 | PASS | Source SHA and all 24 statuses are tested; no new candidate score was written. |
| H01-A04 missing enterprise credentials do not become Mock evidence | PASS | Protected case contract mandates `NOT_RUN/BLOCKED`; all three real executions remain NOT_RUN. |

## Validation performed

- The final focused execution-identity, independent-scoring, acceptance, H01 and project-status set
  passed 114 tests (52.32 seconds).
- The final candidate's complete local Python suite passed 3011 tests with 285 explicit
  environment-gated skips and no failures under the exact CI coverage command (511.33 seconds;
  79.62% coverage) on Python 3.12.14.
- Ruff passed all repository files; 1,124 files passed formatting; strict mypy passed 289 source
  files; all contract, evaluation, release-candidate and secret gates passed with zero secret findings.
- Clean JavaScript install, lint, typecheck, 297 tests and production builds passed before the first
  H01 code publication.
- Both digest-pinned candidate images built from locked inputs and passed the exact Trivy image gate
  with zero High/Critical vulnerabilities and zero secret findings. The control-plane installed-package
  snapshot matched the source package, the existing PostgreSQL schema remained at migration head, and
  the recreated non-root API became healthy.
- GitHub Actions run 35439987679 completed all 15 jobs successfully for the exact runtime candidate:
  quality finished every Python and JavaScript step, all migration/round-trip, Helm and Java jobs
  passed, and the dependent release/container job passed both image scans.

## Migration and rollback

Database migration: **none**. Existing Profile v1, reports, Runs and Events are unchanged. To roll back
the local API, restore the previously recorded revision/image pins and recreate only the API container;
no database downgrade is needed. Profile v2 evidence must not be rewritten into v1 and will fail closed
on a runtime that cannot provide its fields.

## Open gates

- Three protected vertical executions are NOT_RUN; they require operator-controlled cases, authorized
  enterprise sources and, for code execution, an attested sandbox environment.
- The 24 historical cases are a regression set already inspected by developers, not a new holdout.
- The 60-case scoring calibration and 240-case independent business holdout remain H19 release gates.
- Image signing, staging/UAT, production authorization, human security/data-owner approvals and all
  Phase 99 gates remain blocked. H01 cannot be cited as product readiness or factual-accuracy proof.
