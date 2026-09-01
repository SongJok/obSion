"""Phase 87: scheduled CI drill signal for the backup/restore ladders."""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "drill.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_drill_workflow_is_scheduled_and_manual_only() -> None:
    document = _workflow()
    assert document["name"] == "DR drill"
    triggers = document[True] if True in document else document["on"]
    assert "schedule" in triggers
    assert triggers["schedule"] == [{"cron": "17 3 * * *"}]
    assert "workflow_dispatch" in triggers
    # Deliberately not a PR/push gate: the drill pulls pinned registry images
    # and must never make external registry health a merge blocker.
    assert "pull_request" not in triggers
    assert "push" not in triggers
    assert document["permissions"] == {"contents": "read"}


def test_drill_workflow_runs_both_ladders_fail_closed() -> None:
    document = _workflow()
    jobs = document["jobs"]
    assert set(jobs) == {"backup-restore-drill"}
    job = jobs["backup-restore-drill"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] <= 30
    assert job["env"]["OBSION_DR_DRILL"] == "1"
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert any("record-drill-evidence" in run for run in runs)
    assert any("record-artifact-drill-evidence" in run for run in runs)


def test_drill_workflow_never_overwrites_committed_ledgers() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "docs/release/evidence" not in raw
    assert "$RUNNER_TEMP/backup-restore-drill.yaml" in raw
    assert "$RUNNER_TEMP/artifact-store-drill.yaml" in raw
    for forbidden in ("git push", "git commit", "docker push"):
        assert forbidden not in raw


def test_drill_workflow_carries_no_credentials() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "OBSION_FEISHU",
        "OBSION_DINGTALK",
        "OBSION_WECOM",
        "worker.txt",
        "ci.txt",
        "secrets.",
        "MINIO_ROOT",
        "POSTGRES_PASSWORD",
    ):
        assert forbidden not in raw


def test_drill_workflow_uploads_fresh_ledgers_as_short_retention_artifacts() -> None:
    document = _workflow()
    steps = document["jobs"]["backup-restore-drill"]["steps"]
    uploads = [
        step
        for step in steps
        if isinstance(step.get("uses"), str) and "upload-artifact" in step["uses"]
    ]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["uses"].startswith("actions/upload-artifact@v6")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "drill-ledgers"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] <= 14


def test_drill_workflow_pins_actions_like_the_main_pipeline() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@v6" in raw
    assert "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9" in raw
    assert "actions/setup-python@v6" in raw
    assert "uv sync --locked --all-packages --all-extras" in raw


def test_release_notes_and_project_status_track_phase87() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.87.0-dev.yaml", ROOT)
    assert result["version"] == "0.87.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.89.0-dev"
    assert status["current_phase"] == "phase-89"
    assert "phase-87" in status["completed_phases"]
