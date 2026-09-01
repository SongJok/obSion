from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import yaml

from obsion.cli import build_parser
from obsion.release.notes import validate_release_notes

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPOSITORY_ROOT / "docs/release/0.81.0-dev.yaml"


def test_send_live_validation_requires_explicit_opt_in_and_chat_id() -> None:
    environment = dict(os.environ)
    for name in (
        "OBSION_FEISHU_SEND_LIVE",
        "OBSION_FEISHU_APP_ID",
        "OBSION_FEISHU_APP_SECRET",
        "OBSION_FEISHU_LIVE_CHAT_ID",
    ):
        environment.pop(name, None)
    make = shutil.which("make")
    assert make is not None
    result = subprocess.run(  # noqa: S603 - fixed local Make target, no user input
        [make, "validate-feishu-send-live"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "OBSION_FEISHU_SEND_LIVE=1 is required" in result.stdout
    assert "app_secret" not in result.stdout.casefold()


def test_send_live_target_is_bounded_to_one_explicit_message() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    im_tests = (REPOSITORY_ROOT / "apps/im-adapter/tests/test_feishu.py").read_text(
        encoding="utf-8"
    )
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    example_environment = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")

    target = makefile.split("validate-feishu-send-live:", 1)[1].split("\n\n", 1)[0]
    assert "OBSION_FEISHU_SEND_LIVE=1 is required" in target
    assert "OBSION_FEISHU_APP_ID is required" in target
    assert "OBSION_FEISHU_APP_SECRET is required" in target
    assert "OBSION_FEISHU_LIVE_CHAT_ID is required" in target
    assert "pytest --no-cov -m feishu_send_live" in target
    assert "worker.txt" not in target

    assert "feishu_send_live" in pyproject
    assert "OBSION_FEISHU_LIVE_CHAT_ID=" in example_environment
    assert "@pytest.mark.feishu_send_live\nasync def test_feishu_send_live_reply" in im_tests
    assert 'OBSION_FEISHU_SEND_LIVE") != "1"' in im_tests
    assert "OBSION_FEISHU_LIVE_CHAT_ID" in im_tests


def test_chat_listing_is_read_only_and_bounded() -> None:
    client_source = (REPOSITORY_ROOT / "apps/im-adapter/src/obsion_im/feishu.py").read_text(
        encoding="utf-8"
    )
    assert 'CHATS_PATH = "/open-apis/im/v1/chats"' in client_source
    assert "MAX_CHAT_PAGE_SIZE = 100" in client_source
    assert '"GET",' in client_source
    assert "worker.txt" not in client_source
    send_live_section = client_source.split("async def list_chats", 1)[1]
    assert "page_size" in send_live_section
    assert "POST" not in send_live_section.split("async def aclose", 1)[0]


def test_phase81_manifest_is_valid_and_the_cli_default() -> None:
    result = validate_release_notes(MANIFEST_PATH, REPOSITORY_ROOT)

    assert result["name"] == "feishu-live-reply-validation"
    assert result["version"] == "0.81.0-dev"
    assert result["phase"] == 81
    assert result["consolidates"] == [75, 76, 77, 78, 79, 80]
    assert result["database_migration"] == "none"
    assert result["vendors"] == ["feishu"]
    assert "OBSION_FEISHU_LIVE_CHAT_ID" in result["environment_variables"]

    args = build_parser().parse_args(["validate-release-notes"])
    assert args.manifest != "docs/release/0.81.0-dev.yaml"
    assert args.manifest == "docs/release/0.96.0-dev.yaml"

    document = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert "repositoryEvidence" not in document["spec"]


def test_project_status_tracks_phase81_and_the_next_artifact_phase() -> None:
    status = yaml.safe_load(
        (REPOSITORY_ROOT / "docs/project-status.yaml").read_text(encoding="utf-8")
    )
    assert "phase-81" in status["completed_phases"]
    assert status["current_phase"] != "phase-81"
    assert status["next_phase"]["id"] != "phase-81"
