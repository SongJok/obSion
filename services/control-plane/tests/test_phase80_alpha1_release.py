from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from obsion.cli import build_parser
from obsion.release.notes import ReleaseNotesError, validate_release_notes

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY_ROOT / "docs/release/0.80.0-alpha.1.yaml"
RETROSPECTIVE_PHASES = (*range(1, 15), *range(16, 21))


def _manifest() -> dict[str, Any]:
    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write_manifest(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "alpha1.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_alpha1_manifest_binds_complete_repository_evidence() -> None:
    result = validate_release_notes(MANIFEST_PATH, REPOSITORY_ROOT)

    assert result["name"] == "alpha1-repository-release"
    assert result["version"] == "0.80.0-alpha.1"
    assert result["phase"] == 80
    assert result["consolidates"] == list(range(1, 80))
    assert result["database_migration"] == "alembic"
    assert result["migration_head"] == "a79c4d2e8f10"
    assert result["migration_revisions"] == 30
    assert result["phase_reports"] == 80
    assert result["architecture_reviews"] == 80
    assert result["release_stage"] == "alpha"
    assert result["externally_published"] is False
    assert result["signed_tag"] is False
    assert set(result["vendors"]) == {"feishu", "dingtalk", "wecom", "confluence"}

    args = build_parser().parse_args(["validate-release-notes"])
    assert args.manifest == "docs/release/0.80.0-alpha.1.yaml"


def test_alpha1_vendor_and_retrospective_documents_are_explicit_and_secret_free() -> None:
    document = _manifest()
    vendors = {item["id"]: item for item in document["spec"]["vendors"]}
    assert "experience" not in vendors["confluence"]
    assert vendors["confluence"]["knowledge"]["connector"] == "obsion-confluence"

    example_environment = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    for vendor in vendors.values():
        for name in vendor["environmentVariables"]:
            assert f"{name}=" in example_environment

    for phase in RETROSPECTIVE_PHASES:
        report = (REPOSITORY_ROOT / f"docs/phases/PHASE-{phase:02d}-REPORT.md").read_text(
            encoding="utf-8"
        )
        assert "Retrospective" in report


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda document: document["metadata"].__setitem__("version", "0.80.0-alpha.2"),
            "match project status",
        ),
        (
            lambda document: document["spec"]["migration"]["revisions"].pop(),
            "linear repository chain",
        ),
        (
            lambda document: document["spec"]["repositoryEvidence"].__setitem__(
                "phaseReportsDirectory", "docs/missing-phase-reports"
            ),
            "directory does not exist",
        ),
        (
            lambda document: document["spec"]["repositoryEvidence"]["publication"].__setitem__(
                "externallyPublished", True
            ),
            "cannot claim an external publication",
        ),
        (
            lambda document: document["spec"]["vendors"][-1].pop("knowledge"),
            "require experience or knowledge",
        ),
    ],
)
def test_alpha1_repository_evidence_fails_closed(
    tmp_path: Path,
    mutate: Any,
    expected: str,
) -> None:
    document = deepcopy(_manifest())
    mutate(document)
    with pytest.raises(ReleaseNotesError, match=expected):
        validate_release_notes(_write_manifest(tmp_path, document), REPOSITORY_ROOT)
