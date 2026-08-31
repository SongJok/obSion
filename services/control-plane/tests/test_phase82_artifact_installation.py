from __future__ import annotations

import ast
from pathlib import Path

import yaml

from obsion.cli import build_parser
from obsion.release.notes import validate_release_notes

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY_ROOT / "docs/release/0.82.0-dev.yaml"
SCRIPT_PATH = REPOSITORY_ROOT / "scripts/release_artifacts.py"

_STDLIB_MODULES = {
    "argparse",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "pathlib",
    "re",
    "shutil",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "urllib",
    "venv",
}


def _script_tree() -> ast.Module:
    return ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))


def test_release_artifact_script_is_stdlib_only_and_shell_free() -> None:
    tree = _script_tree()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module.split(".")[0])
    assert imports <= _STDLIB_MODULES | {"__future__"}, imports - _STDLIB_MODULES

    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "os.environ" not in source
    assert "worker.txt" not in source
    assert "SECRET" not in source
    assert 'dist" / "release"' in source or '"dist", "release"' in source


def test_release_artifact_script_is_bounded_and_local() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "timeout=timeout" in source
    assert "TimeoutExpired" in source
    assert "externallyPublished" in source
    for forbidden in ("docker push", "npm publish", "uv publish", "twine", "git push"):
        assert forbidden not in source
    assert "eclipse-temurin:21-jdk" in source
    assert "docs/project-status.yaml" in source or "project-status.yaml" in source
    assert "sourceClean" in source
    assert "--allow-dirty" in source
    assert "--require-clean" in source
    assert "uv.lock hashes" in source
    assert "--require-hashes" in source


def test_make_targets_build_and_validate_release_artifacts() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "release-artifacts:" in makefile
    assert "validate-release-artifacts:" in makefile
    build_target = makefile.split("release-artifacts:", 1)[1].split("\n\n", 1)[0]
    validate_target = makefile.split("validate-release-artifacts:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/release_artifacts.py build" in build_target
    assert "scripts/release_artifacts.py validate" in validate_target
    phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY"))
    assert "release-artifacts" in phony
    assert "validate-release-artifacts" in phony
    assert "worker.txt" not in build_target + validate_target


def test_artifact_outputs_stay_gitignored() -> None:
    gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "dist/" in gitignore
    manifest_documents = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))["spec"][
        "documents"
    ]
    assert not any(document.startswith("dist/") for document in manifest_documents)


def test_phase82_manifest_remains_valid_after_the_cli_default_advances() -> None:
    result = validate_release_notes(MANIFEST_PATH, REPOSITORY_ROOT)

    assert result["name"] == "alpha1-artifact-installation"
    assert result["version"] == "0.82.0-dev"
    assert result["phase"] == 82
    assert result["consolidates"] == [75, 76, 77, 78, 79, 80, 81]
    assert result["database_migration"] == "none"
    assert result["vendors"] == []

    args = build_parser().parse_args(["validate-release-notes"])
    assert args.manifest == "docs/release/0.86.0-dev.yaml"

    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "repositoryEvidence" not in document["spec"]


def test_project_status_preserves_phase82_after_phase83_completion() -> None:
    status = yaml.safe_load(
        (REPOSITORY_ROOT / "docs/project-status.yaml").read_text(encoding="utf-8")
    )
    assert "phase-82" in status["completed_phases"]
    assert status["current_phase"] == "phase-86"
    assert status["next_phase"]["id"] == "phase-87"
