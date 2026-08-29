import time

from fastapi.testclient import TestClient


def _create_thread(client: TestClient, title: str) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": title, "description": "Phase 7 harness acceptance"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": title},
    )
    assert thread.status_code == 201, thread.text
    return thread.json()


def _create_turn(client: TestClient, thread_id: str, prompt: str) -> str:
    response = client.post(f"/api/v1/threads/{thread_id}/turns", json={"input": prompt})
    assert response.status_code == 202, response.text
    return str(response.json()["run"]["id"])


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    run = {}
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_general_agent_completes_evidence_free_greeting_loop(client: TestClient) -> None:
    thread = _create_thread(client, "Phase 7 greeting")
    run_id = _create_turn(client, thread["id"], "你好")

    run = _wait_terminal(client, run_id)

    assert run["status"] == "COMPLETED"
    assert run["intent"]["route"] == "CONVERSATION"
    assert run["intent"]["intent"] == "CONVERSATION"
    assert run["plan"] == {
        "route": "CONVERSATION",
        "steps": [],
        "required_evidence": [],
        "verification": ["non_factual_response"],
    }
    assert run["step_count"] == 5

    steps = client.get(f"/api/v1/runs/{run_id}/steps").json()
    step_lifecycle = [
        (step["ordinal"], step["kind"], step["status"], step["depends_on"]) for step in steps
    ]
    assert step_lifecycle == [
        (1, "OBSERVE", "COMPLETED", []),
        (2, "UNDERSTAND", "COMPLETED", [1]),
        (3, "PLAN", "COMPLETED", [2]),
        (4, "VERIFY", "COMPLETED", [3]),
        (5, "RESPOND", "COMPLETED", [4]),
    ]
    assert client.get(f"/api/v1/runs/{run_id}/evidence").json() == []
    assert client.get(f"/api/v1/runs/{run_id}/claims").json() == []

    events = client.get(f"/api/v1/runs/{run_id}/events").json()
    event_names = [event["name"] for event in events]
    assert event_names.index("context.resolved") < event_names.index("intent.detected")
    assert event_names.index("intent.detected") < event_names.index("plan.created")
    assert {"critic.completed", "answer.delta", "artifact.created", "run.completed"} <= set(
        event_names
    )
    assert "tool.started" not in event_names

    artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()
    assert len(artifacts) == 1
    assert artifacts[0]["inline_content"]["verification"]["verified"] is True
    assert "你好" in artifacts[0]["inline_content"]["markdown"]


def test_production_database_request_fails_without_capability_binding(
    client: TestClient,
) -> None:
    thread = _create_thread(client, "Phase 7 production access")
    run_id = _create_turn(client, thread["id"], "查生产库")

    run = _wait_terminal(client, run_id)

    assert run["status"] == "FAILED"
    assert run["error_code"] == "capabilities_unavailable"
    assert run["intent"]["route"] == "RESOURCE_ACCESS"
    assert run["intent"]["need_data"] is True
    assert run["plan"]["route"] == "RESOURCE_ACCESS"
    assert run["plan"]["required_evidence"] == ["DATA"]
    assert run["plan"]["steps"][0]["capability"] == "data.query"
    assert run["plan"]["steps"][0]["environment"] == "production"
    assert "sql" not in run["plan"]["steps"][0]["payload"]

    steps = client.get(f"/api/v1/runs/{run_id}/steps").json()
    assert [(step["ordinal"], step["kind"], step["status"]) for step in steps] == [
        (1, "OBSERVE", "COMPLETED"),
        (2, "UNDERSTAND", "COMPLETED"),
        (3, "PLAN", "COMPLETED"),
        (4, "CAPABILITY", "FAILED"),
        (5, "VERIFY", "SKIPPED"),
        (6, "RESPOND", "SKIPPED"),
    ]
    assert steps[3]["error_code"] == "resource_not_found"
    assert steps[4]["error_code"] == "dependency_failed"
    assert steps[5]["error_code"] == "dependency_failed"
    assert client.get(f"/api/v1/runs/{run_id}/evidence").json() == []
    assert client.get(f"/api/v1/runs/{run_id}/claims").json() == []
    assert client.get(f"/api/v1/runs/{run_id}/artifacts").json() == []

    events = client.get(f"/api/v1/runs/{run_id}/events").json()
    event_names = [event["name"] for event in events]
    assert {"context.resolved", "intent.detected", "plan.created", "run.failed"} <= set(event_names)
    assert "answer.delta" not in event_names
    assert "run.completed" not in event_names
    assert "tool.started" not in event_names
