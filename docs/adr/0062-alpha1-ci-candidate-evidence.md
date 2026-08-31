# ADR 0062: Alpha.1 candidate evidence is clean-source, CI-retained, and promotion-neutral

- Status: Accepted
- Date: 2026-08-31

## Context

Phase 82 proved that all Alpha.1 artifact classes can be built and loaded locally, but
the proof was not part of CI. Its manifest recorded `HEAD` without proving that the
working tree matched that revision, image identities were not normalized as artifact
digests, requirements traceability was a human Markdown matrix, and outstanding
staging/UAT work was documented but not machine-separated from repository readiness.

For an open-source release candidate, a green unit suite is insufficient. The exact
checked-out revision must produce complete installable artifacts, all requirements
must remain attached to shipped surfaces and test evidence, and missing external
approval must fail closed without blocking ordinary repository development.

## Decision

Release artifact builds now refuse a dirty git tree by default and record
`release.sourceClean`. `--allow-dirty` exists only for development diagnostics and
produces a manifest that the candidate validator rejects. Maven runs `clean package`
to remove stale-version JARs, and container image ids are recorded as `sha256`
artifact digests. The public `make test-java` target likewise uses the pinned JDK 21
container with `clean test`, eliminating host-JDK and stale-class ambiguity.

Python clean-room installation uses `uv export --locked` with workspace packages
omitted, installs the resulting hash-pinned runtime graph into a fresh venv, installs
the four built wheels with `--no-deps`, and runs `uv pip check`. This avoids host
`pip` certificate/cache behavior and prevents a candidate from being validated
against dependency versions newer than the committed lock.

Add `ReleaseCandidateGate` and the `obsion validate-release-candidate` command. The
contract declares the exact twelve Alpha.1 artifact identities, mandatory clean-room
steps, and four coverage surfaces. Every unique row in
`docs/product/requirements-traceability.md` must map exactly once to one of those
surfaces and to existing repository evidence. Full validation verifies the manifest
revision, source cleanliness, file hashes, image digests, artifact set, and unskipped
validation steps. A bounded JSON candidate report may be written next to the manifest.

Operator-owned promotion prerequisites are independent gates. `PENDING` gates cannot
carry evidence or approval metadata. `SATISFIED` gates require redacted evidence under
`docs/release/evidence/alpha1/` plus accountable approval metadata. The default
validation reports readiness and exits
success with pending gates; `--require-promotion-eligible` fails until every gate is
satisfied. This distinction prevents CI from fabricating staging, UAT, DR, signatures,
live infrastructure, human sign-off, or publication authority.

The CI container job now depends on ordinary quality, migrations, Java, and Helm;
builds and validates the exact release artifacts; runs candidate validation and Trivy
against the resulting image tags; and retains `dist/release/` as a fourteen-day CI
artifact. It never pushes a package, image, tag, or deployment.

## Consequences

- Every clean CI revision produces auditable Alpha.1 artifact hashes and an exact
  requirements-to-artifact coverage result.
- A dirty local tree can still be diagnosed explicitly, but can never masquerade as
  release evidence.
- Adding or renaming a requirements row requires an intentional candidate coverage
  update; silently untraced scope fails CI.
- The repository is Alpha.1 candidate-ready while production promotion remains
  blocked by six explicit operator gates.
- Phase 83 adds no runtime, Event, API, database, production write, credential, or
  second-control-plane surface.
