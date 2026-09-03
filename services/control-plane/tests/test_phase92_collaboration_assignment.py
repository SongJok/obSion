"""Phase 92: Collaboration workbench assignment and source-Run provenance.

Live API tests pin the readable member identity now carried by
WorkspaceMemberView and the assignee/source-Run validation behaviour the new
Web selectors depend on; static tests pin the Workbench wiring: member
selector, source-Run selector, provenance display, and the cross-view link
that opens a source Run in the Runtime inspector.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"
API = ROOT / "services" / "control-plane" / "src" / "obsion" / "api"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": name, "description": "Assignment provenance coverage"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _provision_user(client: TestClient, external_id: str, display_name: str) -> str:
    response = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": external_id,
            "email": f"{external_id}@obsion.dev",
            "display_name": display_name,
            "attributes": {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def member_workspace(client: TestClient) -> tuple[dict, str]:
    user_id = _provision_user(client, "phase92-assignee", "Phase92 Assignee")
    workspace = _workspace(client, "Assignment provenance")
    member = client.put(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"user_id": user_id, "permissions": ["read", "write"]},
    )
    assert member.status_code == 200, member.text
    return workspace, user_id


def test_member_views_carry_readable_identity(
    client: TestClient, member_workspace: tuple[dict, str]
) -> None:
    workspace, user_id = member_workspace
    members = client.get(f"/api/v1/workspaces/{workspace['id']}/members")
    assert members.status_code == 200, members.text
    by_user = {item["user_id"]: item for item in members.json()}
    assert user_id in by_user
    view = by_user[user_id]
    assert view["display_name"] == "Phase92 Assignee"
    assert view["email"] == "phase92-assignee@obsion.dev"
    for item in by_user.values():
        assert item["display_name"]
        assert "@" in item["email"]


def test_assignee_must_be_an_active_workspace_member(client: TestClient) -> None:
    outsider = _provision_user(client, "phase92-outsider", "Phase92 Outsider")
    workspace = _workspace(client, "Assignee validation")
    created = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        json={"title": "Invalid assignee", "assignee_id": outsider},
    )
    assert created.status_code == 422
    assert created.json()["code"] == "workspace_task_assignee_invalid"

    accepted = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        json={"title": "Owner assigned", "assignee_id": workspace["owner_id"]},
    )
    assert accepted.status_code == 201, accepted.text

    reassigned = client.patch(
        f"/api/v1/workspace-tasks/{accepted.json()['id']}",
        json={"expected_version": 1, "assignee_id": outsider},
    )
    assert reassigned.status_code == 422
    assert reassigned.json()["code"] == "workspace_task_assignee_invalid"


def test_assignee_can_be_cleared_with_explicit_null(
    client: TestClient, member_workspace: tuple[dict, str]
) -> None:
    workspace, user_id = member_workspace
    created = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        json={"title": "Clearable assignment", "assignee_id": user_id},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["assignee_id"] == user_id

    cleared = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": task["version"], "assignee_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["assignee_id"] is None
    assert cleared.json()["version"] == task["version"] + 1


def test_source_run_must_belong_to_the_same_workspace(client: TestClient) -> None:
    origin = _workspace(client, "Run origin")
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": origin["id"], "title": "Origin investigation"},
    )
    assert thread.status_code == 201, thread.text
    turn = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "Investigate the payment timeout"},
    )
    assert turn.status_code == 202, turn.text
    run_id = turn.json()["run"]["id"]

    other = _workspace(client, "Unrelated workspace")
    mismatched = client.post(
        f"/api/v1/workspaces/{other['id']}/tasks",
        json={"title": "Cross-workspace provenance", "source_run_id": run_id},
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["code"] == "workspace_source_run_mismatch"

    linked = client.post(
        f"/api/v1/workspaces/{origin['id']}/tasks",
        json={"title": "Linked provenance", "source_run_id": run_id},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["source_run_id"] == run_id

    decision = client.post(
        f"/api/v1/workspaces/{other['id']}/decisions",
        json={
            "title": "Cross-workspace decision",
            "summary": "Must fail.",
            "rationale": "The source run belongs elsewhere.",
            "source_run_id": run_id,
        },
    )
    assert decision.status_code == 422
    assert decision.json()["code"] == "workspace_source_run_mismatch"


def test_member_view_schema_exposes_identity_fields() -> None:
    schemas = (API / "schemas.py").read_text(encoding="utf-8")
    block = schemas.split("class WorkspaceMemberView", 1)[1]
    assert "display_name: str" in block
    assert "email: str" in block
    endpoints = (API / "workspaces.py").read_text(encoding="utf-8")
    assert "_member_views" in endpoints
    assert "User.id.in_" in endpoints


def test_web_facade_and_types_cover_members() -> None:
    facade = _read("src/lib/api.ts")
    assert "listWorkspaceMembers" in facade
    assert "`/workspaces/${workspaceId}/members`" in facade
    types = _read("src/lib/types.ts")
    assert "export interface WorkspaceMember" in types
    assert "display_name: string" in types


def test_collaboration_view_wires_assignment_and_provenance() -> None:
    view = _read("src/components/collaboration-view.tsx")
    for marker in (
        "fetchSourceRunOptions",
        "api.listWorkspaceMembers",
        "memberDisplayName(members, task.assignee_id)",
        "sourceRunLabel(sourceRuns, task.source_run_id)",
        "sourceRunLabel(sourceRuns, decision.source_run_id)",
        "taskCreatePayload",
        "taskUpdatePayload",
        "taskUpdateHasChanges",
        "editingTask",
        "指派给",
        "来源 Run（可选）",
        "task-source-run",
        "onOpenRun?: (runId: string, threadId?: string) => void",
        "sourceRunThreadId(sourceRuns, task.source_run_id)",
        "sourceRunThreadId(sourceRuns, decision.source_run_id)",
        "workspace_task_assignee_invalid",
        "workspace_source_run_mismatch",
    ):
        assert marker in view


def test_workbench_opens_source_runs_through_their_owning_thread() -> None:
    workbench = _read("src/components/workbench.tsx")
    assert "const openScopedRun" in workbench
    assert "api.listThreads(workspaceId)" in workbench
    assert "await openThread(selected, runId)" in workbench
    assert "loadInspection(target, workspaceId)" in workbench
    assert "assertRunWorkspace(target, workspaceId)" in workbench
    assert "generation !== selectionGeneration.current" in workbench
    assert "onOpenRun={(runId, threadId) => void openScopedRun(runId, threadId)}" in workbench


def test_display_helpers_and_styles_exist() -> None:
    helpers = _read("src/lib/collaboration-display.ts")
    for marker in (
        "MAX_SOURCE_RUN_OPTIONS",
        "expected_version: task.version",
        "payload.assignee_id = assigneeId",
        "toDateTimeLocalValue",
        "sourceRunLabel",
        "sourceRunThreadId",
        "threadId",
    ):
        assert marker in helpers
    styles = _read("src/app/globals.css")
    assert ".task-assignee" in styles
    assert ".task-source-run" in styles
    inspector = _read("src/components/runtime-inspector.tsx")
    assert 'ANALYTICS: "经营分析"' in inspector
    assert 'OPERATION: "运维分析"' in inspector
    assert 'SUPPORT: "支持诊断"' in inspector


def test_web_behaviour_suite_covers_assignment_payloads() -> None:
    suite = _read("tests/collaboration-display.test.ts")
    for marker in (
        "assignee_id: null",
        "taskUpdateHasChanges",
        "MAX_SOURCE_RUN_OPTIONS",
        "成员 abcdef12",
        "Run cafe0001",
    ):
        assert marker in suite


def test_release_notes_and_project_status_track_phase92() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.92.0-dev.yaml", ROOT)
    assert result["version"] == "0.92.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.97.0-dev"
    assert status["current_phase"] == "phase-97"
    assert "phase-92" in status["completed_phases"]
