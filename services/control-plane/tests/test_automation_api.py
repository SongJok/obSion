import time
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": name, "description": "Governed automation workspace"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for_execution(
    client: TestClient,
    execution_id: str,
    expected: set[str],
    *,
    attempts: int = 120,
) -> dict:
    execution: dict = {}
    for _ in range(attempts):
        response = client.get(f"/api/v1/automation/executions/{execution_id}")
        assert response.status_code == 200, response.text
        execution = response.json()
        if execution["status"] in expected:
            return execution
        time.sleep(0.05)
    raise AssertionError(f"execution did not reach {expected}: {execution}")


def _create_review_workflow(client: TestClient, workspace_id: str) -> dict:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/workflows",
        json={
            "name": "daily-payment-watch",
            "display_name": "每日支付监控",
            "description": "异常确认后通知责任人",
            "concurrency_policy": "FORBID",
            "max_concurrency": 1,
            "notify_on_success": True,
            "spec": {
                "steps": [
                    {
                        "id": "review",
                        "name": "人工确认",
                        "type": "HUMAN_REVIEW",
                        "review_instructions": "确认异常分析是否可以继续通知。",
                    },
                    {
                        "id": "notify",
                        "name": "通知责任人",
                        "type": "NOTIFICATION",
                        "depends_on": ["review"],
                        "title": "{{input.service}} 异常分析完成",
                        "body": "运行 {{execution.id}} 已通过人工确认。",
                    },
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workflow_lifecycle_review_idempotency_and_notifications(client: TestClient) -> None:
    workspace = _workspace(client, "Automation control room")
    created = _create_review_workflow(client, workspace["id"])
    workflow_id = created["workflow"]["id"]
    assert created["workflow"]["status"] == "DRAFT"
    assert created["version"]["checksum_sha256"]

    unpublished = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={"input_payload": {"service": "支付"}},
    )
    assert unpublished.status_code == 409, unpublished.text
    assert unpublished.json()["code"] == "workflow_not_active"

    published = client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish")
    assert published.status_code == 200, published.text
    assert published.json()["workflow"]["status"] == "ACTIVE"
    assert published.json()["version"]["published_at"]

    schedule = client.post(
        f"/api/v1/workflows/{workflow_id}/schedules",
        json={
            "name": "weekday-morning",
            "cron_expression": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "input_payload": {"service": "支付"},
            "enabled": True,
        },
    )
    assert schedule.status_code == 201, schedule.text
    assert schedule.json()["next_fire_at"]

    triggered = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={
            "input_payload": {"service": "支付"},
            "idempotency_key": "payment-watch-2026-08-25",
        },
    )
    assert triggered.status_code == 202, triggered.text
    execution_id = triggered.json()["id"]
    duplicate = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={
            "input_payload": {"service": "ignored-on-retry"},
            "idempotency_key": "payment-watch-2026-08-25",
        },
    )
    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json()["id"] == execution_id

    concurrent = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={
            "input_payload": {"service": "支付"},
            "idempotency_key": "payment-watch-2026-08-25-retry",
        },
    )
    assert concurrent.status_code == 202, concurrent.text
    assert concurrent.json()["status"] == "SKIPPED"
    assert concurrent.json()["error_code"] == "workflow_concurrency_forbidden"

    waiting = _wait_for_execution(client, execution_id, {"WAITING_REVIEW"})
    review = next(step for step in waiting["steps"] if step["step_key"] == "review")
    approved = client.post(
        f"/api/v1/automation/steps/{review['id']}/review",
        json={"decision": "APPROVE", "reason": "异常证据已由值班负责人确认"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "COMPLETED"

    completed = _wait_for_execution(client, execution_id, {"COMPLETED"})
    assert completed["summary"]["step_counts"] == {"COMPLETED": 2}
    assert all(step["status"] == "COMPLETED" for step in completed["steps"])

    inbox = client.get("/api/v1/notifications", params={"unread_only": True})
    assert inbox.status_code == 200, inbox.text
    assert {item["title"] for item in inbox.json()} == {
        "支付 异常分析完成",
        "每日支付监控 已完成",
    }
    notification = inbox.json()[0]
    marked = client.post(f"/api/v1/notifications/{notification['id']}/read")
    assert marked.status_code == 200, marked.text
    assert marked.json()["status"] == "READ"
    assert marked.json()["read_at"]

    paused = client.post(f"/api/v1/workflows/{workflow_id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"
    schedules = client.get(f"/api/v1/workflows/{workflow_id}/schedules")
    assert schedules.status_code == 200, schedules.text
    assert schedules.json()[0]["enabled"] is False


def test_analysis_step_uses_harness_and_produces_traceable_run(client: TestClient) -> None:
    workspace = _workspace(client, "Recurring analysis")
    created = client.post(
        f"/api/v1/workspaces/{workspace['id']}/workflows",
        json={
            "name": "release-readiness",
            "display_name": "发布就绪分析",
            "timeout_seconds": 300,
            "spec": {
                "steps": [
                    {
                        "id": "analyze",
                        "name": "分析发布风险",
                        "type": "ANALYSIS",
                        "prompt": "分析 {{input.release}} 的发布风险并给出可验证结论。",
                    }
                ]
            },
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["workflow"]["id"]
    assert client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish").status_code == 200
    triggered = client.post(
        f"/api/v1/workflows/{workflow_id}/trigger",
        json={
            "input_payload": {"release": "2026.08.25"},
            "idempotency_key": "release-readiness-2026-08-25",
        },
    )
    assert triggered.status_code == 202, triggered.text
    completed = _wait_for_execution(client, triggered.json()["id"], {"COMPLETED"})
    step = completed["steps"][0]
    assert step["run_id"]
    assert completed["summary"]["run_ids"] == [step["run_id"]]
    run = client.get(f"/api/v1/runs/{step['run_id']}")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "COMPLETED"
    assert client.get(f"/api/v1/runs/{step['run_id']}/events").json()


def test_workflow_validation_and_permissions_fail_closed(client: TestClient) -> None:
    workspace = _workspace(client, "Automation permissions")
    invalid = client.post(
        f"/api/v1/workspaces/{workspace['id']}/workflows",
        json={
            "name": "cyclic-workflow",
            "display_name": "非法环形工作流",
            "spec": {
                "steps": [
                    {
                        "id": "first",
                        "name": "First",
                        "type": "HUMAN_REVIEW",
                        "depends_on": ["second"],
                        "review_instructions": "Review first",
                    },
                    {
                        "id": "second",
                        "name": "Second",
                        "type": "HUMAN_REVIEW",
                        "depends_on": ["first"],
                        "review_instructions": "Review second",
                    },
                ]
            },
        },
    )
    assert invalid.status_code == 422

    restricted = Principal(
        id=UUID("00000000-0000-7000-8000-000000000002"),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="dev-user",
        display_name="Restricted owner",
        permissions=frozenset({"workspace.read.all", "workspace.manage.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: restricted
    try:
        denied = client.post(
            f"/api/v1/workspaces/{workspace['id']}/workflows",
            json={
                "name": "denied-workflow",
                "display_name": "Denied",
                "spec": {
                    "steps": [
                        {
                            "id": "review",
                            "name": "Review",
                            "type": "HUMAN_REVIEW",
                            "review_instructions": "Review the result",
                        }
                    ]
                },
            },
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "automation_permission_denied"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    client.app.dependency_overrides[get_principal] = lambda: restricted
    try:
        denied_response = client.get("/api/v1/notifications")
        assert denied_response.status_code == 403, denied_response.text
        assert denied_response.json()["code"] == "automation_permission_denied"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
