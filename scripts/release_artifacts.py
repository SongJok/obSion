#!/usr/bin/env python3
"""Build and validate Obsion release artifacts from one repository revision.

This operator tool builds the Python distributions, the TypeScript SDK archive,
the Java SDK JAR, and the control-plane/Workbench container images into
``dist/release/<version>/``, records SHA-256 hashes and image identifiers in an
``artifact-manifest.json``, and then installs or loads every artifact inside a
clean temporary environment to prove the outputs are usable.

The script uses only the standard library, never reads credentials, keeps every
output inside the repository, and never publishes anything externally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_STATUS = REPOSITORY_ROOT / "docs/project-status.yaml"
PYTHON_PACKAGES = (
    "packages/sdk-python",
    "apps/cli",
    "apps/im-adapter",
    "services/control-plane",
)
JAVA_IMAGE = "eclipse-temurin:21-jdk"
UV_BUILD_TIMEOUT = 600
NPM_TIMEOUT = 900
JAVA_TIMEOUT = 1800
IMAGE_BUILD_TIMEOUT = 3600
SMOKE_TIMEOUT = 120
WEB_SMOKE_RETRIES = 30

MANIFEST_API_VERSION = "obsion.ai/v1"
MANIFEST_KIND = "ArtifactManifest"


class ArtifactError(RuntimeError):
    """A build or validation step failed; the message is credential-free."""


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    skipped: bool
    detail: str


def _run(
    command: list[str],
    *,
    cwd: Path = REPOSITORY_ROOT,
    timeout: int,
    step: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument lists, no shell
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ArtifactError(f"{step}: required tool is unavailable: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ArtifactError(f"{step}: timed out after {timeout}s") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
        detail = " | ".join(line.strip() for line in tail if line.strip())[:400]
        raise ArtifactError(f"{step}: exited {result.returncode}: {detail}")
    return result


def _release_version() -> str:
    text = PROJECT_STATUS.read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([0-9A-Za-z.-]+)\s*$", text, re.MULTILINE)
    if not match:
        raise ArtifactError("project status does not declare a semantic version")
    return match.group(1)


def _revision() -> str:
    result = _run(
        ["git", "rev-parse", "HEAD"],
        timeout=30,
        step="revision lookup",
    )
    return result.stdout.strip()


def _source_state(*, allow_dirty: bool) -> tuple[str, bool]:
    revision = _revision()
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        timeout=30,
        step="source tree status",
    )
    clean = not status.stdout.strip()
    if not clean and not allow_dirty:
        raise ArtifactError(
            "source tree is dirty; commit the release inputs or use --allow-dirty "
            "for a non-candidate development build"
        )
    return revision, clean


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_artifact(name: str, kind: str, path: Path, root: Path) -> dict[str, object]:
    return {
        "name": name,
        "type": kind,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "sizeBytes": path.stat().st_size,
    }


def _build_python(out_dir: Path) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for package in PYTHON_PACKAGES:
        _run(
            ["uv", "build", package, "--out-dir", str(out_dir)],
            timeout=UV_BUILD_TIMEOUT,
            step=f"python build {package}",
        )
    for path in sorted(out_dir.iterdir()):
        if path.suffix == ".whl":
            kind = "python-wheel"
        elif path.name.endswith(".tar.gz"):
            kind = "python-sdist"
        else:
            continue
        artifacts.append(_file_artifact(path.stem.split("-")[0], kind, path, REPOSITORY_ROOT))
    if not artifacts:
        raise ArtifactError("python build produced no distributions")
    return artifacts


def _build_node(out_dir: Path) -> list[dict[str, object]]:
    _run(
        ["npm", "--prefix", "packages/sdk-ts", "run", "build"],
        timeout=NPM_TIMEOUT,
        step="node sdk build",
    )
    _run(
        ["npm", "pack", "./packages/sdk-ts", "--pack-destination", str(out_dir)],
        timeout=NPM_TIMEOUT,
        step="node sdk pack",
    )
    archives = sorted(out_dir.glob("*.tgz"))
    if len(archives) != 1:
        raise ArtifactError("node sdk pack did not produce exactly one archive")
    return [_file_artifact("@obsion/sdk", "node-tarball", archives[0], REPOSITORY_ROOT)]


def _build_java(out_dir: Path) -> list[dict[str, object]]:
    _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPOSITORY_ROOT}:/workspace",
            "-w",
            "/workspace/packages/sdk-java",
            JAVA_IMAGE,
            "./mvnw",
            "-B",
            "-DskipTests",
            "clean",
            "package",
        ],
        timeout=JAVA_TIMEOUT,
        step="java sdk package",
    )
    target = REPOSITORY_ROOT / "packages/sdk-java/target"
    jars = sorted(
        path
        for path in target.glob("obsion-sdk-*.jar")
        if not path.name.endswith(("sources.jar", "javadoc.jar"))
    )
    if len(jars) != 1:
        raise ArtifactError("java sdk package did not produce exactly one JAR")
    destination = out_dir / jars[0].name
    shutil.copy2(jars[0], destination)
    return [_file_artifact("obsion-sdk-java", "java-jar", destination, REPOSITORY_ROOT)]


def _build_images(version: str) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for name, dockerfile in (
        ("obsion-control-plane", "deploy/docker/control-plane.Dockerfile"),
        ("obsion-web", "deploy/docker/web.Dockerfile"),
    ):
        tag = f"{name}:{version}"
        _run(
            ["docker", "build", "-f", dockerfile, "-t", tag, "."],
            timeout=IMAGE_BUILD_TIMEOUT,
            step=f"container image build {name}",
        )
        inspect = _run(
            ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
            timeout=30,
            step=f"container image inspect {name}",
        )
        artifacts.append(
            {
                "name": name,
                "type": "container-image",
                "image": tag,
                "imageId": inspect.stdout.strip(),
                "sha256": inspect.stdout.strip(),
            }
        )
    return artifacts


def _verify_file_hashes(manifest: dict[str, object], manifest_dir: Path) -> list[Step]:
    steps: list[Step] = []
    for artifact in _artifacts(manifest):
        if not isinstance(artifact, dict) or "path" not in artifact:
            continue
        path = manifest_dir / str(artifact["path"])
        if not path.is_file():
            raise ArtifactError(f"validate: missing artifact {artifact['path']}")
        actual = _sha256(path)
        if actual != artifact.get("sha256"):
            raise ArtifactError(f"validate: hash mismatch for {artifact['path']}")
        steps.append(Step(f"hash {artifact['name']}", False, actual[:16]))
    return steps


def _artifacts(manifest: dict[str, object]) -> list[object]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ArtifactError("manifest does not contain any artifacts")
    return artifacts


def _manifest_path(artifact: dict[str, object], manifest_dir: Path) -> Path:
    return manifest_dir / str(artifact["path"])


def _python_clean_room(manifest: dict[str, object], manifest_dir: Path) -> list[Step]:
    wheels = [
        _manifest_path(artifact, manifest_dir)
        for artifact in _artifacts(manifest)
        if isinstance(artifact, dict) and artifact.get("type") == "python-wheel"
    ]
    if len(wheels) != len(PYTHON_PACKAGES):
        raise ArtifactError("validate: expected one wheel per Python package")
    steps: list[Step] = []
    with tempfile.TemporaryDirectory(prefix="obsion-artifact-venv-") as temp:
        environment = Path(temp) / "venv"
        requirements = Path(temp) / "runtime-requirements.txt"
        venv.EnvBuilder(with_pip=False, clear=True).create(environment)
        python = environment / "bin" / "python"
        _run(
            [
                "uv",
                "export",
                "--locked",
                "--all-packages",
                "--no-dev",
                "--no-emit-workspace",
                "--format",
                "requirements.txt",
                "--output-file",
                str(requirements),
            ],
            timeout=SMOKE_TIMEOUT,
            step="clean-room locked dependency export",
        )
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-cache",
                "--no-progress",
                "--require-hashes",
                "--requirements",
                str(requirements),
            ],
            timeout=JAVA_TIMEOUT,
            step="clean-room locked dependency install",
        )
        steps.append(Step("clean-room locked dependency install", False, "uv.lock hashes"))
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-cache",
                "--no-progress",
                "--no-deps",
                *(str(wheel) for wheel in wheels),
            ],
            timeout=JAVA_TIMEOUT,
            step="clean-room wheel install",
        )
        steps.append(Step("clean-room wheel install", False, f"{len(wheels)} wheels"))
        _run(
            ["uv", "pip", "check", "--python", str(python)],
            timeout=SMOKE_TIMEOUT,
            step="clean-room dependency check",
        )
        steps.append(Step("clean-room dependency check", False, "installed graph compatible"))
        _run(
            [
                str(python),
                "-c",
                "import obsion, obsion_cli, obsion_im, obsion_sdk",
            ],
            timeout=SMOKE_TIMEOUT,
            step="clean-room import smoke",
        )
        steps.append(Step("clean-room import smoke", False, "obsion* modules import"))
        for entrypoint in ("obsion", "obsion-cli", "obsion-im"):
            _run(
                [str(environment / "bin" / entrypoint), "--help"],
                timeout=SMOKE_TIMEOUT,
                step=f"clean-room {entrypoint} smoke",
            )
            steps.append(Step(f"clean-room {entrypoint} smoke", False, "--help exited 0"))
    return steps


def _node_clean_room(manifest: dict[str, object], manifest_dir: Path) -> list[Step]:
    tarballs = [
        _manifest_path(artifact, manifest_dir)
        for artifact in _artifacts(manifest)
        if isinstance(artifact, dict) and artifact.get("type") == "node-tarball"
    ]
    if len(tarballs) != 1:
        raise ArtifactError("validate: expected exactly one node tarball")
    with tempfile.TemporaryDirectory(prefix="obsion-artifact-node-") as temp:
        _run(
            ["npm", "install", "--prefix", temp, str(tarballs[0])],
            timeout=NPM_TIMEOUT,
            step="clean-room node install",
        )
        _run(
            [
                "node",
                "--input-type=module",
                "-e",
                "const m = await import('@obsion/sdk');"
                " if (typeof m !== 'object' || m === null) process.exit(1);",
            ],
            cwd=Path(temp),
            timeout=SMOKE_TIMEOUT,
            step="clean-room node import smoke",
        )
    return [Step("clean-room node install+import", False, tarballs[0].name)]


def _java_clean_room(manifest: dict[str, object], manifest_dir: Path) -> list[Step]:
    jars = [
        _manifest_path(artifact, manifest_dir)
        for artifact in _artifacts(manifest)
        if isinstance(artifact, dict) and artifact.get("type") == "java-jar"
    ]
    if len(jars) != 1:
        raise ArtifactError("validate: expected exactly one java JAR")
    listing = _run(
        ["jar", "tf", str(jars[0])],
        timeout=SMOKE_TIMEOUT,
        step="clean-room jar load check",
    )
    classes = [line for line in listing.stdout.splitlines() if line.endswith(".class")]
    if not any(line.startswith("dev/obsion/") for line in classes):
        raise ArtifactError("validate: JAR does not contain dev/obsion classes")
    return [Step("clean-room jar load check", False, f"{len(classes)} classes")]


def _image_smoke(manifest: dict[str, object]) -> list[Step]:
    steps: list[Step] = []
    images = {
        str(artifact["image"]): str(artifact["name"])
        for artifact in _artifacts(manifest)
        if isinstance(artifact, dict) and artifact.get("type") == "container-image"
    }
    for tag, name in images.items():
        if name == "obsion-control-plane":
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    "/app/.venv/bin/python",
                    tag,
                    "-c",
                    "import obsion",
                ],
                timeout=SMOKE_TIMEOUT,
                step="control-plane image import smoke",
            )
            steps.append(Step("control-plane image import smoke", False, tag))
        elif name == "obsion-web":
            steps.append(_web_image_smoke(tag))
    return steps


def _web_image_smoke(tag: str) -> Step:
    container = f"obsion-web-smoke-{datetime.now(tz=UTC):%H%M%S}"
    _run(
        ["docker", "run", "-d", "--rm", "--name", container, "-p", "127.0.0.1::3000", tag],
        timeout=SMOKE_TIMEOUT,
        step="web image smoke start",
    )
    try:
        port = (
            _run(
                ["docker", "port", container, "3000"],
                timeout=30,
                step="web image smoke port",
            )
            .stdout.strip()
            .rsplit(":", 1)[-1]
        )
        url = f"http://127.0.0.1:{port}/"
        last_error: Exception | None = None
        for _ in range(WEB_SMOKE_RETRIES):
            try:
                with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - loopback smoke
                    if response.status < 500:
                        return Step("web image http smoke", False, f"{tag} -> {response.status}")
            except Exception as exc:  # noqa: BLE001 - retried bounded loopback probe
                last_error = exc
                import time

                time.sleep(1)
        raise ArtifactError(f"web image smoke: no HTTP response: {last_error}")
    finally:
        _run(["docker", "stop", container], timeout=60, step="web image smoke stop")


def build(args: argparse.Namespace) -> None:
    version = _release_version()
    revision, source_clean = _source_state(allow_dirty=args.allow_dirty)
    out_dir = REPOSITORY_ROOT / "dist" / "release" / version
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "python").mkdir(parents=True)
    (out_dir / "node").mkdir(parents=True)
    (out_dir / "java").mkdir(parents=True)

    artifacts: list[dict[str, object]] = []
    artifacts.extend(_build_python(out_dir / "python"))
    artifacts.extend(_build_node(out_dir / "node"))
    if args.skip_java:
        artifacts.append({"name": "obsion-sdk-java", "type": "java-jar", "skipped": True})
    else:
        artifacts.extend(_build_java(out_dir / "java"))
    if args.skip_images:
        for name in ("obsion-control-plane", "obsion-web"):
            artifacts.append({"name": name, "type": "container-image", "skipped": True})
    else:
        artifacts.extend(_build_images(version))

    final_revision, final_clean = _source_state(allow_dirty=args.allow_dirty)
    if final_revision != revision:
        raise ArtifactError("source revision changed during the artifact build")
    source_clean = source_clean and final_clean

    manifest = {
        "apiVersion": MANIFEST_API_VERSION,
        "kind": MANIFEST_KIND,
        "release": {
            "version": version,
            "revision": revision,
            "sourceClean": source_clean,
            "builtAt": datetime.now(tz=UTC).isoformat(),
            "externallyPublished": False,
        },
        "artifacts": artifacts,
    }
    manifest_path = out_dir / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"built {len(artifacts)} artifacts -> {manifest_path.relative_to(REPOSITORY_ROOT)}")


def validate(args: argparse.Namespace) -> None:
    version = _release_version()
    out_dir = REPOSITORY_ROOT / "dist" / "release" / version
    manifest_path = Path(args.manifest) if args.manifest else out_dir / "artifact-manifest.json"
    if not manifest_path.is_file():
        raise ArtifactError(f"manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("apiVersion") != MANIFEST_API_VERSION or manifest.get("kind") != MANIFEST_KIND:
        raise ArtifactError("manifest apiVersion/kind is invalid")
    release = manifest.get("release")
    if not isinstance(release, dict) or release.get("version") != version:
        raise ArtifactError("manifest release version does not match project status")
    if release.get("externallyPublished") is not False:
        raise ArtifactError("manifest must not claim external publication")
    if type(release.get("sourceClean")) is not bool:
        raise ArtifactError("manifest must declare whether the source tree was clean")
    if args.require_clean and release.get("sourceClean") is not True:
        raise ArtifactError("manifest was built from a dirty source tree")

    steps = _verify_file_hashes(manifest, REPOSITORY_ROOT)
    steps.extend(_python_clean_room(manifest, REPOSITORY_ROOT))
    steps.extend(_node_clean_room(manifest, REPOSITORY_ROOT))
    skipped = [a for a in _artifacts(manifest) if isinstance(a, dict) and a.get("skipped")]
    if skipped:
        steps.extend(
            Step(f"{a['name']}", True, "not built in this run")
            for a in skipped
            if isinstance(a, dict)
        )
    else:
        steps.extend(_java_clean_room(manifest, REPOSITORY_ROOT))
        if not args.skip_images:
            steps.extend(_image_smoke(manifest))

    manifest["validation"] = {
        "validatedAt": datetime.now(tz=UTC).isoformat(),
        "steps": [
            {"name": step.name, "skipped": step.skipped, "detail": step.detail} for step in steps
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for step in steps:
        marker = "skip" if step.skipped else "ok"
        print(f"[{marker}] {step.name}: {step.detail}")
    if any(step.skipped for step in steps):
        print("validation completed with skipped artifact classes; not a full release proof")
    else:
        print("all artifacts validated in clean temporary environments")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="build all release artifacts")
    build_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow a non-candidate development build and record sourceClean=false",
    )
    build_parser.add_argument("--skip-java", action="store_true")
    build_parser.add_argument("--skip-images", action="store_true")
    build_parser.set_defaults(handler=build)
    validate_parser = commands.add_parser(
        "validate", help="validate artifacts in clean temporary environments"
    )
    validate_parser.add_argument("--manifest")
    validate_parser.add_argument("--skip-images", action="store_true")
    validate_parser.add_argument("--require-clean", action="store_true")
    validate_parser.set_defaults(handler=validate)
    args = parser.parse_args()
    try:
        args.handler(args)
    except ArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
