# Phase 83 Alpha.1 release-candidate architecture review

## Review question

Can one clean CI revision produce and retain every Alpha.1 artifact, prove the full V1
requirements matrix is attached to shipped surfaces and tests, and still refuse to
claim production-promotion authority without real operator evidence?

**Status: PASS for repository candidate readiness; PENDING for external promotion.**

## Invariants reviewed

- The release path changes no Harness behavior. Workspace → Thread → Turn → Run →
  Step → Event and Observe → Understand → Plan → Execute → Verify → Reflect → Respond
  remain authoritative.
- All runtime external access remains Capability Gateway → Policy → connector. Release
  tooling reads no connector credentials and cannot enable a Capability.
- A candidate manifest must match a full git SHA and declare `sourceClean: true`.
  Dirty or skipped builds, missing files, mismatched hashes, incomplete image digests,
  and absent clean-room steps fail closed.
- The candidate gate expects exactly twelve artifacts: four Python wheels, four
  Python sdists, one TypeScript SDK tarball, one Java SDK JAR, and two images.
- Python clean-room validation exports hash-pinned runtime dependencies from the
  committed `uv.lock`, installs them and the four wheels through `uv`, and verifies
  dependency compatibility; it does not silently resolve newer package versions.
- Each of the 37 unique requirements rows maps exactly once through the control-plane,
  Workbench, client/SDK, or open-source distribution surface and points to existing
  repository test evidence.
- The six external promotion prerequisites are stateful only as reviewed documents.
  Pending gates cannot claim evidence; satisfied gates require evidence and named
  approval. No prompt, CI variable, or package manifest can override them.

## CI acceptance map

- Quality validates the static candidate contract alongside contracts, evaluations,
  release notes, datasets, secret scan, Python tests, and frontend tests.
- Migrations retain isolated PostgreSQL upgrade/downgrade/drift checks.
- Java SDK and Helm jobs remain independent prerequisites.
- The dependent artifact job builds from the clean checkout, clean-room validates all
  artifact classes, writes the candidate report, scans the exact two images for
  unfixed CRITICAL findings, and uploads repository-local evidence for fourteen days.
- CI contains no publish, push, tag, signing, staging, or deployment command.

An isolated clean local snapshot also exercised the complete path: all 12 artifacts
built, all 21 manifest validation steps passed, Workbench returned HTTP 200, and the
candidate report validated all 37 requirement rows. Promotion-required mode still
failed on the six pending operator gates, preserving the external boundary.

## Migration and rollback

There is no Alembic or Event migration. Rollback is reverting the release-automation
commit and discarding the CI artifact bundle. Existing release contracts, Runs,
Events, Evidence, Claims, Artifacts, and Audit records remain immutable.

## External gate

Phase 84 remains blocked until real staging/UAT, timed restore, registry CVE/signature,
live identity/secret/read-replica, security/data-owner, and maintainer publication
evidence is supplied under explicit authority. CI readiness alone is not production
approval.
