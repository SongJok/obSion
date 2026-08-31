from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from obsion.cli import build_parser
from obsion.release.hardening import cyclonedx_sbom
from obsion.release.notes import ReleaseNotesError, read_project_version, validate_release_notes

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY_ROOT / "docs/release/0.75.0-dev.yaml"


def _manifest() -> dict[str, Any]:
    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _write_manifest(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "release.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_repository_release_notes_are_complete_and_versioned() -> None:
    result = validate_release_notes(MANIFEST_PATH, REPOSITORY_ROOT)
    assert result["name"] == "vendor-integration-consolidation"
    assert result["version"] == "0.75.0-dev"
    assert result["phase"] == 75
    assert result["consolidates"] == list(range(68, 75))
    assert result["database_migration"] == "none"
    assert set(result["vendors"]) == {"feishu", "dingtalk", "wecom"}
    assert result["rollout_steps"] >= 8
    assert result["rollback_steps"] >= 6
    assert result["verification_checks"] >= 5

    changelog = (REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.index("Phase 75") < changelog.index("Phase 74")


def test_vendor_release_contract_matches_sources_and_connector_manifests() -> None:
    document = _manifest()
    vendors = {item["id"]: item for item in document["spec"]["vendors"]}
    implementation_files = {
        "feishu": (
            "apps/im-adapter/src/obsion_im/feishu.py",
            "services/control-plane/src/obsion/capabilities/feishu_docs.py",
            "connectors/examples/feishu-docs.yaml",
        ),
        "dingtalk": (
            "apps/im-adapter/src/obsion_im/dingtalk.py",
            "services/control-plane/src/obsion/capabilities/dingtalk_docs.py",
            "connectors/examples/dingtalk-docs.yaml",
        ),
        "wecom": (
            "apps/im-adapter/src/obsion_im/wecom.py",
            "services/control-plane/src/obsion/capabilities/wecom_docs.py",
            "connectors/examples/wecom-docs.yaml",
        ),
    }
    example_environment = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    release_text = (REPOSITORY_ROOT / "docs/release/0.75.0-dev.md").read_text(encoding="utf-8")

    for vendor, (im_path, knowledge_path, connector_path) in implementation_files.items():
        contract = vendors[vendor]
        im_source = (REPOSITORY_ROOT / im_path).read_text(encoding="utf-8")
        knowledge_source = (REPOSITORY_ROOT / knowledge_path).read_text(encoding="utf-8")
        assert f'ORIGIN = "{contract["experience"]["outboundOrigin"]}"' in im_source
        assert f'ORIGIN = "{contract["knowledge"]["origin"]}"' in knowledge_source

        connector = yaml.safe_load((REPOSITORY_ROOT / connector_path).read_text(encoding="utf-8"))
        assert connector["metadata"]["name"] == contract["knowledge"]["connector"]
        assert connector["spec"]["baseUrl"] == contract["knowledge"]["origin"]
        assert connector["spec"]["allowedEgress"] == [contract["knowledge"]["origin"]]
        assert connector["spec"]["capabilities"] == contract["knowledge"]["operations"]

        for environment_name in contract["environmentVariables"]:
            assert f"{environment_name}=" in example_environment
        assert vendor in release_text.casefold()

    for phase in range(68, 76):
        assert f"Phase {phase}" in release_text


def test_operator_guidance_has_no_pre_phase68_support_claims() -> None:
    administrator = (REPOSITORY_ROOT / "docs/operators/administrator.md").read_text(
        encoding="utf-8"
    )
    runbook = (REPOSITORY_ROOT / "docs/operators/runbook.md").read_text(encoding="utf-8")
    assert "DingTalk HTTP, and WeCom HTTP remain rejected" not in administrator
    assert "WeCom AES ciphertext fails closed" not in administrator
    assert "Do not add vendor HTTP clients" not in runbook
    assert "dingtalk-http" in administrator
    assert "wecom-http" in runbook
    assert "0.75.0-dev release notes" in administrator


def test_sbom_uses_authoritative_project_status_version(tmp_path: Path) -> None:
    lockfile = tmp_path / "uv.lock"
    lockfile.write_text(
        '[[package]]\nname = "httpx"\nversion = "0.28.1"\n',
        encoding="utf-8",
    )
    project_version = read_project_version(REPOSITORY_ROOT / "docs/project-status.yaml")
    sbom = cyclonedx_sbom(lockfile, component_version=project_version)
    assert sbom["metadata"]["component"]["version"] == project_version

    args = build_parser().parse_args(
        ["validate-release-notes", "--manifest", "docs/release/0.75.0-dev.yaml"]
    )
    assert args.manifest == "docs/release/0.75.0-dev.yaml"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda document: document["spec"].__setitem__("consolidates", [68, 69, 71, 72, 73, 74]),
            "contiguous",
        ),
        (
            lambda document: document["spec"]["vendors"][0].__setitem__(
                "environmentVariables", ["OBSION_FEISHU_APP_SECRET=value"]
            ),
            "names only",
        ),
        (
            lambda document: document["spec"]["vendors"][0]["knowledge"].__setitem__(
                "origin", "https://open.feishu.cn/open-apis"
            ),
            "bare HTTPS origins",
        ),
        (
            lambda document: document["spec"]["migration"].__setitem__(
                "revisions", ["not-a-real-revision"]
            ),
            "no-migration",
        ),
        (
            lambda document: document["spec"].__setitem__("documents", ["../worker.txt"]),
            "inside the repository",
        ),
    ],
)
def test_release_notes_validator_fails_closed(
    tmp_path: Path,
    mutate: Any,
    expected: str,
) -> None:
    document = deepcopy(_manifest())
    mutate(document)
    with pytest.raises(ReleaseNotesError, match=expected):
        validate_release_notes(_write_manifest(tmp_path, document), REPOSITORY_ROOT)
