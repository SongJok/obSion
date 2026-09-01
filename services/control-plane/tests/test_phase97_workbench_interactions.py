"""Phase 97: broader Workbench interaction tests.

The Web test stack now exercises real component interactions — composer
keyboard behaviour, claim-action provenance, and the collaboration
task-creation flow — against a mocked API boundary. Writing those tests
surfaced a genuine defect: the collaboration view's mutation handler set
its actionable error message and *then* refreshed, and the refresh clears
notices on entry, so version-conflict, assignee-invalid, and source-Run
mismatch guidance vanished before anyone could read it. The handler now
refreshes first and surfaces the message after; the interaction suite
pins the ordering so the guidance cannot silently regress.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def test_interaction_suite_covers_three_workbench_surfaces() -> None:
    suite = _read("tests/workbench-interactions.test.tsx")
    for marker in (
        "Composer interactions",
        "RuntimeInspector claim actions",
        "CollaborationView task creation",
        "fireEvent",
        "cleanup",
        "停止运行",
        "添加上下文 支付周报",
        "移除附件 支付周报",
        "source_run_id",
        "在协作中查看",
        "workspace_source_run_mismatch",
        "workspace_task_assignee_invalid",
        "指派的成员必须是该工作空间的在职成员，请刷新成员列表后重试。",
    ):
        assert marker in suite


def test_interaction_suite_pins_notice_survival_after_refresh() -> None:
    suite = _read("tests/workbench-interactions.test.tsx")
    assert "keeps the version-conflict guidance visible after the refresh" in suite
    assert "记录已被其他成员更新，已为你刷新到最新版本。请确认后重试。" in suite
    assert "listTasks.mock.calls.length" in suite


def test_mutation_handler_refreshes_before_surfacing_guidance() -> None:
    view = _read("src/components/collaboration-view.tsx")
    # Every ApiError branch must refresh first and surface the message after,
    # because load() clears notices on entry.
    assert view.count("await load();\n          setError(") == 3
    assert 'setError("记录已被其他成员更新' in view
    assert 'setError("指派的成员必须是该工作空间的在职成员' in view
    assert 'setError("来源 Run 必须属于当前工作空间' in view


def test_release_notes_and_project_status_track_phase97() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.97.0-dev.yaml", ROOT)
    assert result["version"] == "0.97.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.97.0-dev"
    assert status["current_phase"] == "phase-97"
    assert "phase-96" in status["completed_phases"]
