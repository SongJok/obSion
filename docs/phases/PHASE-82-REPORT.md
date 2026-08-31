# PHASE-82-REPORT — Alpha.1 artifact build and clean-room installation

## What was implemented

- Added `scripts/release_artifacts.py`, a standard-library-only operator tool
  with `build` and `validate` subcommands, fixed argument lists (no shell),
  bounded timeouts on every external call, and no credential access.
- `build` compiles from one repository revision: the four Python distributions
  (`obsion-sdk`, `obsion-cli`, `obsion-im`, `obsion-control-plane` wheels and
  sdists via `uv build`), the `@obsion/sdk` TypeScript tarball (`npm run build`
  + `npm pack`), the Java SDK JAR (`./mvnw -B -DskipTests package` inside the
  pinned `eclipse-temurin:21-jdk` container, since host JDKs may be older than
  the required 21), and the `obsion-control-plane` / `obsion-web` container
  images from the committed Dockerfiles.
- All outputs land in the gitignored `dist/release/<version>/` tree, where
  `<version>` is read from `docs/project-status.yaml`. SHA-256 hashes, sizes,
  image tags/ids, the git revision, and `externallyPublished: false` are
  recorded in `artifact-manifest.json`.
- `validate` re-verifies every hash, installs all wheels into a fresh temporary
  venv and import-smokes `obsion`, `obsion_cli`, `obsion_im`, `obsion_sdk` plus
  the `obsion` / `obsion-cli` / `obsion-im` entry points, installs the Node
  tarball into a temporary npm prefix and imports `@obsion/sdk`, lists the JAR
  to prove it contains `dev/obsion` classes, and smoke-runs both images locally
  (control-plane import check; bounded loopback HTTP probe for the Workbench).
  Validation results are appended to the manifest; any failure exits non-zero.
- Added `make release-artifacts` and `make validate-release-artifacts`,
  aligned the Java SDK version to `0.1.0` with the other packages, and relaxed
  the release-notes validator to permit an empty `vendors` list for
  artifact-only releases (per-vendor contract rules unchanged when vendors are
  declared).
- Published the `0.82.0-dev` machine/human release contracts as the CLI
  default, ADR 0061, the phase-82 architecture gate, runbook maintenance
  guidance, roadmap, and a regenerated SBOM.

## Architecture decisions

ADR 0061 keeps the Alpha.1 artifact proof local and deterministic: one revision,
gitignored outputs, no push/tag/publish, clean-room installation as the
acceptance bar, and CVE scanning left to CI (Trivy) alongside the existing SBOM
boundary. Package versions stay at `0.1.0` during the development line; the
release version is carried by the artifact manifest and image tags.

## Migration

No database or Event migration is added. The manifest declares
`migration.database: none` and Alembic drift checks continue to pass.

## Validation

- `make check` passed: Ruff format/lint, strict mypy, contract/Event/evaluation
  validation, release-note validation against the new `0.82.0-dev` default
  manifest, dataset execution, zero secret findings, frontend lint/typecheck,
  the full Python suite, Desktop/IDE/TypeScript SDK tests, and Alembic drift.
- `test_phase82_artifact_installation.py` — stdlib-only/shell-free/bounded
  script guards, Make target wiring, gitignored outputs, manifest validity, CLI
  default, and project-status tracking: 6 passed.
- `test_phase80_alpha1_release.py`, `test_phase81_feishu_live_reply.py`, and
  `test_phase75_release_notes.py` — frozen Alpha.1 contract, 0.81.0-dev
  development contract, and validator rules after the empty-vendors relaxation:
  all passed.
- `make release-artifacts` executed on this revision: 8 Python distributions,
  1 Node tarball, 1 Java JAR, and 2 container images built into
  `dist/release/0.82.0-dev/`.
- `make validate-release-artifacts` executed: every hash verified, clean venv
  install + import/CLI smokes passed, Node tarball install + ESM import passed,
  JAR contained `dev/obsion` classes, control-plane image import smoke passed,
  and the Workbench image answered the loopback HTTP probe. Results recorded in
  `dist/release/0.82.0-dev/artifact-manifest.json`.

## Remaining risks

- Artifacts remain repository-local; external publication, signing, staging
  promotion, UAT, and human security sign-off are still operator-owned.
- The artifact manifest is rebuilt per run and is not yet reproduced inside CI;
  wiring it into CI is declared as Phase 83 scope.
- The control-plane image smoke proves import/CLI startup inside the container;
  a full stack boot still requires the documented compose environment.
- Package versions (`0.1.0`) intentionally trail the release line
  (`0.82.0-dev`) until the external publication phase.
