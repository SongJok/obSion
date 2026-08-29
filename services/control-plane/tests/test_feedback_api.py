import time
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def _completed_run(client: TestClient) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Feedback workspace", "description": "Satisfaction evidence"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Feedback lifecycle"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "Summarize this request with verifiable evidence."},
    )
    assert created.status_code == 202, created.text
    run = created.json()["run"]
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run['id']}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert run["status"] == "COMPLETED", run
    return run


def test_run_feedback_is_versioned_idempotent_and_auditable(client: TestClient) -> None:
    run = _completed_run(client)
    endpoint = f"/api/v1/runs/{run['id']}/feedback"

    empty = client.get(endpoint)
    assert empty.status_code == 200, empty.text
    assert empty.json() is None

    created = client.put(endpoint, json={"rating": "HELPFUL"})
    assert created.status_code == 200, created.text
    first = created.json()
    assert first["rating"] == "HELPFUL"
    assert first["reason"] == ""
    assert first["version"] == 1

    loaded = client.get(endpoint)
    assert loaded.status_code == 200
    assert loaded.json() == first

    before_idempotent = client.get(f"/api/v1/runs/{run['id']}/events").json()
    repeated = client.put(endpoint, json={"rating": "HELPFUL"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["version"] == 1
    after_idempotent = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert after_idempotent == before_idempotent

    revised = client.put(
        endpoint,
        json={
            "rating": "NEEDS_IMPROVEMENT",
            "reason": "Bearer definitely-not-a-real-token was not relevant enough.",
            "expected_version": 1,
        },
    )
    assert revised.status_code == 200, revised.text
    second = revised.json()
    assert second["rating"] == "NEEDS_IMPROVEMENT"
    assert second["reason"] == "Bearer [REDACTED] was not relevant enough."
    assert second["version"] == 2

    stale = client.put(
        endpoint,
        json={"rating": "HELPFUL", "expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "run_feedback_version_conflict"
    assert stale.json()["details"]["current_version"] == 2

    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert [event["run_sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["name"] for event in events[-2:]] == [
        "run.feedback.recorded",
        "run.feedback.revised",
    ]
    assert events[-1]["payload"] == {
        "rating": "NEEDS_IMPROVEMENT",
        "reason_provided": True,
        "feedback_version": 2,
    }

    summary = client.get("/api/v1/admin/feedback/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        "total": 1,
        "helpful": 0,
        "needs_improvement": 1,
        "helpful_rate": 0.0,
    }


def test_run_feedback_preserves_tenant_boundary(client: TestClient) -> None:
    run = _completed_run(client)
    other_tenant = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-feedback",
        display_name="Cross-tenant Feedback",
        permissions=frozenset({"workspace.read.all", "audit.read"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other_tenant
    try:
        endpoint = f"/api/v1/runs/{run['id']}/feedback"
        assert client.get(endpoint).status_code == 404
        assert client.put(endpoint, json={"rating": "HELPFUL"}).status_code == 404
        summary = client.get("/api/v1/admin/feedback/summary")
        assert summary.status_code == 200
        assert summary.json()["total"] == 0
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
