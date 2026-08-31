# Phase 82 Alpha.1 artifact build and clean-room installation review

## Review question

Can every Alpha.1 artifact class be built from one repository revision and
installed or loaded in an environment that shares nothing with the development
workspace—without publishing anything externally, adding a second runtime, or
weakening any Policy boundary?

**Status: PENDING external publication — artifacts, hashes, and smoke results are
repository-local evidence in `dist/release/<version>/`; staging promotion,
signing, and CVE scanning remain CI/operator-owned.**

## Build and validation contract

- `scripts/release_artifacts.py build` compiles the four Python distributions
  (sdk-python, cli, im-adapter, control-plane) via `uv build`, the TypeScript
  SDK tarball via `npm run build` + `npm pack`, the Java SDK JAR via
  `./mvnw package` inside the pinned `eclipse-temurin:21-jdk` container, and
  the control-plane/Workbench images via the committed Dockerfiles. All outputs
  land in the gitignored `dist/release/<version>/` tree keyed by the
  `docs/project-status.yaml` version.
- Every file artifact is SHA-256 hashed and every image recorded by tag and
  image id in `artifact-manifest.json`, which pins the git revision and asserts
  `externallyPublished: false`.
- `validate` re-verifies hashes, installs all wheels into a fresh temporary
  venv and import/CLI-smokes every package, installs the Node tarball into a
  temporary npm prefix and imports `@obsion/sdk`, lists the JAR for
  `dev/obsion` classes, and smoke-runs both images locally (control-plane
  import check; bounded loopback HTTP probe for the Workbench). Any failure
  exits non-zero.
- The tool uses only the Python standard library, fixed argument lists (no
  shell), bounded timeouts, and never reads credentials.
- `docs/release/0.82.0-dev.yaml` is the machine-validated contract for this
  phase; the release-notes validator now permits an empty `vendors` list
  because an artifact-only release has no vendor surface.

## Automated acceptance map

- `services/control-plane/tests/test_phase82_artifact_installation.py` proves
  the script's fail-closed guards (stdlib-only imports, no shell invocation,
  bounded timeouts, local-only outputs), the Make target wiring, the manifest
  contract, and project-status tracking.
- `test_phase80_alpha1_release.py` and `test_phase81_feishu_live_reply.py`
  continue to prove the frozen Alpha.1 contract and the 0.81.0-dev development
  manifest after the default moved to 0.82.0-dev.
- `make release-artifacts` + `make validate-release-artifacts` were executed on
  this revision; the recorded manifest is the execution evidence.
- `make check`, secret scanning, and release-note validation cover the revision.

## Migration review

Phase 82 adds no database revision and no Event version. Rollback is deleting
`dist/release/<version>/` and the local image tags; no remote state exists.

## Human review checklist

- Confirm `artifact-manifest.json` hashes match the files on disk and that the
  validation section records no failed step.
- Confirm no image was pushed and no package index upload occurred.
- Keep external publication, staging promotion, and Trivy CVE gating as
  explicit operator/CI-owned steps before any wider distribution.
