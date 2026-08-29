import time

from fastapi.testclient import TestClient


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_turn_and_audit_never_persist_prompt_secrets(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 10 privacy", "description": "Audit privacy boundary"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Prompt privacy"},
    )
    assert thread.status_code == 201, thread.text

    prompt_fragment = "password='phase10-never-store' api_key=phase10-api-secret"
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": f"Please greet me; {prompt_fragment}"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED"

    turns = client.get(f"/api/v1/threads/{thread.json()['id']}/turns")
    assert turns.status_code == 200, turns.text
    stored = turns.json()[0]["input_text"]
    assert "phase10-never-store" not in stored
    assert "phase10-api-secret" not in stored
    assert "[REDACTED]" in stored


def test_run_completion_audit_exposes_canonical_dimensions(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 10 audit", "description": "Audit dimensions"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Audit dimensions"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED"

    audit = client.get("/api/v1/admin/audit?limit=100")
    assert audit.status_code == 200, audit.text
    completion = next(
        item
        for item in audit.json()
        if item["action"] == "run.complete" and item["resource_id"] == run["id"]
    )
    assert completion["actor_type"] == "SYSTEM"
    assert completion["correlation_id"] == run["id"]
    assert completion["latency_ms"] is not None
    metadata = completion["metadata"]
    assert metadata["agent_version_id"] == run["agent_version_id"]
    assert metadata["model_profile_id"] == run["model_profile_id"]
    assert metadata["resource"]["run_id"] == run["id"]
    assert metadata["result_classification"] == "INTERNAL"
