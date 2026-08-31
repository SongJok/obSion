# ADR 0061: Alpha.1 artifacts are built from one revision and proven in clean rooms

- Status: Accepted
- Date: 2026-08-31

## Context

The repository-wide Alpha.1 contract (Phase 80) froze a static release manifest,
and Phases 75-81 hardened the runtime, but no phase has yet proven that the
codebase actually produces installable artifacts. An open-source runtime that
cannot be built and installed from a clean checkout is not releasable: wheels
may miss package data, the Node tarball may ship stale `dist/` output, the Java
JAR may require a newer JDK than contributors have, and the container images may
fail to start outside the development shell.

External publication is still out of scope, so the proof must stay local:
build every artifact class from one revision, record hashes, and install or load
each artifact in an environment that shares nothing with the development
workspace.

## Decision

Add `scripts/release_artifacts.py`, a standard-library-only operator tool with
two subcommands, exposed as `make release-artifacts` and
`make validate-release-artifacts`.

`build` compiles, from the repository root of the current revision: the four
Python distributions (`uv build` for sdk-python, cli, im-adapter,
control-plane), the TypeScript SDK tarball (`npm run build` then `npm pack`),
the Java SDK JAR (`./mvnw package` inside the pinned `eclipse-temurin:21-jdk`
container, because host JDKs may be older than the required 21), and the
control-plane and Workbench container images (`docker build` with the committed
Dockerfiles). Every output lands in the gitignored `dist/release/<version>/`
tree, where `<version>` is read from `docs/project-status.yaml`; file artifacts
are hashed (SHA-256) and images are recorded by tag and image id in
`artifact-manifest.json`, which also pins the git revision and asserts
`externallyPublished: false`.

`validate` re-verifies every hash, installs all four wheels into a fresh
temporary venv and import-smokes every package plus the `obsion`, `obsion-cli`,
and `obsion-im` entry points, installs the Node tarball into a temporary npm
prefix and imports `@obsion/sdk`, lists the JAR to prove it contains
`dev/obsion` classes, and smoke-runs both images locally (import check for the
control plane; bounded loopback HTTP probe for the Workbench). Results are
appended to the manifest. Any failure exits non-zero; nothing is ever pushed,
tagged, or published.

Package versions stay at `0.1.0` for the development line; the release version
lives in the manifest metadata and image tags. Image/dependency CVE scanning
remains a CI (Trivy) responsibility, matching the existing SBOM boundary.

## Consequences

- Alpha.1 now has a repeatable local build-and-install proof for every artifact
  class; future phases can extend the same script rather than inventing new
  release tooling.
- The release-notes validator now permits an empty `vendors` list because an
  artifact-only release has no vendor surface; per-vendor contract rules are
  unchanged when vendors are declared.
- `dist/release/` stays gitignored; the manifest is evidence for operators, not
  a published contract.
- Version alignment between packages (`0.1.0`) and the release line is
  deferred to the external publication phase and recorded as a known
  limitation.
- No runtime, Event, API, database, Agent, Capability, production write, or
  credential boundary changes in this ADR.
