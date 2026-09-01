"""Phase 94: Automation Web authoring depth.

Live API tests pin the backend capabilities the deepened Workbench authoring
surface now exposes end to end — immutable version round-trips, re-publishing
an older version, trigger input payloads with idempotency, and schedules
pinned to a fixed version. Static tests pin the Web wiring: the versions
card, spec viewer, authoring modal, trigger/schedule payload editors, the
retire action, and the Harness run link in the execution drawer.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": name, "description": "Automation authoring coverage"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _spec(prompt: str, review: bool = False) -> dict:
    steps: list[dict] = [
        {"id": "analyze", "name": "智能分析", "type": "ANALYSIS", "prompt": prompt}
    ]
    if review:
        steps.append(
            {
                "id": "review",
                "name": "人工确认",
                "type": "HUMAN_REVIEW",
                "depends_on": ["analyze"],
                "review_instructions": "检查分析结论、证据覆盖和通知范围。",
                "disallow_self_review": True,
            }
        )
    steps.append(
        {
            "id": "notify",
            "name": "通知责任人",
            "type": "NOTIFICATION",
            "depends_on": ["review" if review else "analyze"],
            "title": "分析已完成",
            "body": "周期分析已完成，请查看运行详情中的证据与产物。",
        }
    )
    return {"steps": steps}


def _create_workflow(client: TestClient, workspace_id: str, name: str) -> dict:
    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/workflows",
        json={
            "name": name,
            "display_name": name,
            "description": "authoring depth coverage",
            "concurrency_policy": "FORBID",
            "max_concurrency": 1,
            "timeout_seconds": 3600,
            "notify_on_failure": True,
            "classification": "INTERNAL",
            "spec": _spec("分析过去 24 小时的支付成功率"),
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_version_roundtrip_and_republish(client: TestClient) -> None:
    workspace = _workspace(client, "Authoring versions")
    created = _create_workflow(client, workspace["id"], "authoring-versions")
    workflow_id = created["workflow"]["id"]
    assert created["version"]["spec"]["steps"][0]["type"] == "ANALYSIS"
    assert created["version"]["checksum_sha256"]
    assert created["version"]["created_by"]

    versions = client.get(f"/api/v1/workflows/{workflow_id}/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1]

    v2 = client.post(
        f"/api/v1/workflows/{workflow_id}/versions",
        json={"spec": _spec("分析过去 7 天的支付成功率", review=True)},
    )
    assert v2.status_code == 201, v2.text
    assert v2.json()["version"] == 2
    assert len(v2.json()["spec"]["steps"]) == 3
    assert v2.json()["checksum_sha256"] != created["version"]["checksum_sha256"]

    assert client.post(f"/api/v1/workflows/{workflow_id}/versions/2/publish").status_code == 200
    workflow = client.get(f"/api/v1/workflows/{workflow_id}").json()
    assert workflow["active_version"] == 2

    republished = client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish")
    assert republished.status_code == 200, republished.text
    assert republished.json()["workflow"]["active_version"] == 1


def test_trigger_input_payload_echoes_and_is_idempotent(client: TestClient) -> None:
    workspace = _workspace(client, "Authoring trigger")
    workflow_id = _create_workflow(client, workspace["id"], "authoring-trigger")["workflow"]["id"]
    assert client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish").status_code == 200

    payload = {"day": "2026-09-01", "window": "24h"}
    first = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={"input_payload": payload, "idempotency_key": "phase94-trigger-key"},
    )
    assert first.status_code == 202, first.text
    assert first.json()["input_payload"] == payload
    assert first.json()["idempotency_key"] == "phase94-trigger-key"

    replay = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={"input_payload": payload, "idempotency_key": "phase94-trigger-key"},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first.json()["id"]


def test_schedule_can_pin_a_fixed_version_with_payload(client: TestClient) -> None:
    workspace = _workspace(client, "Authoring schedules")
    workflow_id = _create_workflow(client, workspace["id"], "authoring-schedule")["workflow"]["id"]
    assert client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish").status_code == 200

    schedule = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules",
        json={
            "name": "每工作日晨报",
            "cron_expression": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "misfire_policy": "FIRE_ONCE",
            "misfire_grace_seconds": 300,
            "input_payload": {"window": "24h"},
            "workflow_version": 1,
            "enabled": True,
        },
    )
    assert schedule.status_code == 201, schedule.text
    body = schedule.json()
    assert body["input_payload"] == {"window": "24h"}
    assert body["timezone"] == "Asia/Shanghai"
    assert body["enabled"] is True

    invalid = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules",
        json={
            "name": "不存在的版本",
            "cron_expression": "0 9 * * *",
            "timezone": "UTC",
            "workflow_version": 99,
        },
    )
    assert invalid.status_code in {404, 409, 422}


def test_retire_blocks_further_publication(client: TestClient) -> None:
    workspace = _workspace(client, "Authoring retire")
    workflow_id = _create_workflow(client, workspace["id"], "authoring-retire")["workflow"]["id"]
    assert client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish").status_code == 200
    assert client.post(f"/api/v1/workflows/{workflow_id}/pause").status_code == 200
    retired = client.post(f"/api/v1/workflows/{workflow_id}/retire")
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "RETIRED"
    republish = client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish")
    assert republish.status_code == 409
    assert republish.json()["code"] == "workflow_retired"


def test_web_facade_covers_authoring_depth() -> None:
    facade = _read("src/lib/api.ts")
    assert "createVersion" in facade
    assert "`/workflows/${workflowId}/versions`" in facade
    assert "idempotencyKey ?? `web-${crypto.randomUUID()}`" in facade
    types = _read("src/lib/types.ts")
    for marker in (
        "export interface WorkflowStepSpec",
        "export interface WorkflowSpec",
        "disallow_self_review?: boolean",
        "spec: WorkflowSpec;",
        "created_by: string;",
    ):
        assert marker in types


def test_automation_view_wires_authoring_depth() -> None:
    view = _read("src/components/automation-view.tsx")
    for marker in (
        "api.automation.createVersion",
        "api.automation.listVersions",
        "sortedVersions(versions)",
        "versionStepSummary(parseWorkflowSpec(version.spec))",
        "parseWorkflowSpec(base.spec)",
        "buildSpecFromDraft(draft, workflowName)",
        "parseInputPayload(text)",
        "buildSchedulePayload({",
        "CRON_PRESETS.map",
        "确认退役",
        "workflow-retired-note",
        "outputRefLabel(ref)",
        "step-run-link",
        "onOpenRun={onOpenRun}",
        "当前发布版本",
        "跟随当前发布版本",
    ):
        assert marker in view


def test_workbench_opens_automation_runs_in_the_inspector() -> None:
    workbench = _read("src/components/workbench.tsx")
    assert (
        '<AutomationView key={workspace?.id ?? "no-workspace"} workspace={workspace}'
        " onOpenRun={(runId) => void openRunInspection(runId)} />" in workbench
    )


def test_authoring_helpers_and_styles_exist() -> None:
    helpers = _read("src/lib/automation-authoring.ts")
    for marker in (
        "buildSpecFromDraft",
        "draftFromSpec",
        "parseWorkflowSpec",
        "versionStepSummary",
        "parseInputPayload",
        "buildSchedulePayload",
        "cronIsValid",
        "outputRefLabel",
        "artifactOutputRefs",
    ):
        assert marker in helpers
    styles = _read("src/app/globals.css")
    for marker in (".version-row", ".output-ref-chip", ".spec-step", ".execution-input"):
        assert marker in styles


def test_web_behaviour_suite_covers_authoring() -> None:
    suite = _read("tests/automation-authoring.test.ts")
    for marker in (
        "buildSpecFromDraft",
        "draftFromSpec",
        "parseInputPayload",
        "buildSchedulePayload",
        "分析 → 人工确认 → 通知",
        "每周一 09:00",
    ):
        assert marker in suite


def test_release_notes_and_project_status_track_phase94() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.94.0-dev.yaml", ROOT)
    assert result["version"] == "0.94.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.94.0-dev"
    assert status["current_phase"] == "phase-94"
    assert "phase-93" in status["completed_phases"]
