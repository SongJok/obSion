# H01 architecture gate: comparable candidate baseline

Date: 2026-09-19
Decision: **PASS for repository-local architecture; real protected cases remain NOT_RUN**
ADR: [0143](../adr/0143-comparable-harness-candidate-baseline.md)

## Boundary reviewed

H01 changes the identity and evidence contract around the existing Python control plane. It does not
add a second backend, a second Agent loop, a new transactional store, or a direct connector path.
PostgreSQL remains the durable Harness truth source; Capability Gateway, Policy Engine, Credential
Broker and existing Run events retain their roles.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| Candidate cannot reuse a different commit | PASS | Acceptance Profile v2 binds the full commit and rejects a mismatch before task creation. |
| API package is observed, not inferred from a label | PASS | API initialization records package SHA256/file count; runtime identity reports the observation and `signature_verified=false`. |
| Actual Worker execution remains independently checked | PASS | Each evaluated Run must contain its own `run.execution_observed`; the image package is only the expected value. |
| Image, models, connectors, prompts, capabilities, agents, skills and policy are frozen | PASS | The development candidate ledger records the image and content-free endpoint digests; Profile v2 rechecks them pre/post flight. |
| Connector snapshot excludes transient health and protected configuration | PASS | Admin response contains only ID/name/status/digest; repeated unchanged reads are equal. Endpoint, configuration and credential references are hashed server-side and not returned. |
| Authorization boundary is unchanged | PASS | Snapshot routes require existing `admin.read`; credentials remain Broker-only and no Agent receives them. |
| Historical Profile/report compatibility | PASS | Profile v1 remains accepted and byte-compatible; no history is rewritten or upgraded in place. |
| Historical 24-case denominator is immutable | PASS | Source SHA and 5 PASS / 1 FAIL / 18 BLOCKED projection are verified by tests. |
| Failure diagnosis does not guess | PASS | Seven requested categories plus `UNCLASSIFIED`; machine diagnostic wins, unknown reasons remain unknown. |
| Clean JavaScript workspace is deterministic | PASS | Root quality commands build the TypeScript SDK before consumers import its declarations. |
| Release images retain no known High/Critical finding at freeze time | PASS | Digest-pinned Python/Node Alpine 3.24 runtimes pass Trivy 0.70.0 with `ignore-unfixed=false`; the standalone Web layer excludes npm/Corepack/Yarn. |
| Real business localization case | NOT_RUN | Protected enterprise source and holdout case were not supplied; no Mock substituted. |
| Real exact-statistics case | NOT_RUN | Protected gold data and authorized query source were not supplied; no Mock substituted. |
| Real isolated-code-execution case | NOT_RUN | Protected repository case and attested sandbox runtime were not supplied; no Mock substituted. |
| Production promotion | BLOCKED | Unsigned development image, no protected vertical executions, P1 quality not calibrated, and Phase 99 gates remain open. |

## Invariants

1. Profile drift is a comparability failure, never a score.
2. Package/configuration hashes are observations, not signatures or remote attestation.
3. PASS, FAIL, BLOCKED and NOT_RUN all remain in the denominator appropriate to their contract;
   absence is not converted to success.
4. Connector configuration and enterprise content never enter the public evidence ledger.
5. H01 does not broaden production access or change action risk levels.

## Migration and rollback

No schema migration is required. Rolling back the runtime removes Profile-v2 production support,
but existing v2 evidence must remain immutable and fail closed against a v1 runtime. Profile v1 and
all existing Run/Event data remain readable. The local deployment can roll back to the previously
recorded image and revision without a database downgrade.

## Follow-on

H02 may build the task contract only on this single Harness. The three protected case contracts must
be carried forward and executed when their real sources and environments are authorized; H19 still
owns the independent 60-case calibration and 240-case holdout release gate.
