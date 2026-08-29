from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def _workspace(client: TestClient, name: str = "Incident command") -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": name, "description": "Governed coordination records"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_task_lifecycle_is_versioned_and_auditable(client: TestClient) -> None:
    workspace = _workspace(client)
    created = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        json={
            "title": "Verify customer impact",
            "description": "Use Bearer definitely-not-a-real-token as a test fixture",
            "priority": "CRITICAL",
            "assignee_id": workspace["owner_id"],
            "due_at": "2026-08-26T02:00:00+08:00",
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "OPEN"
    assert task["version"] == 1
    assert task["description"] == "Use Bearer [REDACTED] as a test fixture"
    assert task["due_at"] == "2026-08-25T18:00:00Z"

    filtered = client.get(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        params={"status": "OPEN", "assignee_id": workspace["owner_id"]},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [task["id"]]

    started = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": 1, "status": "IN_PROGRESS"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["version"] == 2
    assert started.json()["completed_at"] is None

    stale = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": 1, "priority": "HIGH"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "workspace_task_version_conflict"
    assert stale.json()["details"]["current_version"] == 2

    completed = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": 2, "status": "COMPLETED"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["version"] == 3
    assert completed.json()["completed_at"] is not None

    invalid = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": 3, "status": "CANCELLED"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "workspace_task_transition_invalid"

    reopened = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": 3, "status": "OPEN", "due_at": None},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["version"] == 4
    assert reopened.json()["completed_at"] is None
    assert reopened.json()["due_at"] is None

    no_change = client.patch(
        f"/api/v1/workspace-tasks/{task['id']}",
        json={"expected_version": 4, "status": "OPEN"},
    )
    assert no_change.status_code == 409
    assert no_change.json()["code"] == "workspace_task_no_changes"

    events = client.get(f"/api/v1/workspace-tasks/{task['id']}/events")
    assert events.status_code == 200
    assert [item["sequence"] for item in events.json()] == [1, 2, 3, 4]
    assert [item["name"] for item in events.json()] == [
        "workspace_task.created",
        "workspace_task.updated",
        "workspace_task.updated",
        "workspace_task.updated",
    ]


def test_workspace_decisions_preserve_versions_and_supersession_lineage(
    client: TestClient,
) -> None:
    workspace = _workspace(client, "Architecture council")
    proposed = client.post(
        f"/api/v1/workspaces/{workspace['id']}/decisions",
        json={
            "title": "Adopt append-only evidence records",
            "summary": "Persist evidence as governed immutable records.",
            "rationale": "Replay and audit require stable historical inputs.",
            "alternatives": ["Mutable documents", "External-only logs"],
        },
    )
    assert proposed.status_code == 201, proposed.text
    first = proposed.json()
    assert first["status"] == "PROPOSED"
    assert first["current_version"] == 1
    assert len(first["checksum_sha256"]) == 64

    revised = client.patch(
        f"/api/v1/workspace-decisions/{first['id']}",
        json={
            "expected_version": 1,
            "title": first["title"],
            "summary": "Persist evidence and its lineage as immutable records.",
            "rationale": first["rationale"],
            "alternatives": first["alternatives"],
        },
    )
    assert revised.status_code == 200, revised.text
    current = revised.json()
    assert current["current_version"] == 2
    assert current["checksum_sha256"] != first["checksum_sha256"]

    stale = client.post(
        f"/api/v1/workspace-decisions/{first['id']}/accept",
        json={"expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "workspace_decision_version_conflict"

    accepted = client.post(
        f"/api/v1/workspace-decisions/{first['id']}/accept",
        json={"expected_version": 2},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["decided_by"] == workspace["owner_id"]

    closed_revision = client.patch(
        f"/api/v1/workspace-decisions/{first['id']}",
        json={
            "expected_version": 2,
            "title": first["title"],
            "summary": first["summary"],
            "rationale": first["rationale"],
            "alternatives": first["alternatives"],
        },
    )
    assert closed_revision.status_code == 409
    assert closed_revision.json()["code"] == "workspace_decision_revision_closed"

    versions = client.get(f"/api/v1/workspace-decisions/{first['id']}/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [2, 1]
    assert [item["checksum_sha256"] for item in versions.json()] == [
        current["checksum_sha256"],
        first["checksum_sha256"],
    ]

    replacement = client.post(
        f"/api/v1/workspaces/{workspace['id']}/decisions",
        json={
            "title": "Adopt content-addressed evidence records",
            "summary": "Retain immutability and add deterministic content identities.",
            "rationale": "Content identity improves deduplication without weakening auditability.",
            "alternatives": ["Keep the prior record format"],
            "supersedes_decision_id": first["id"],
        },
    )
    assert replacement.status_code == 201, replacement.text
    accepted_replacement = client.post(
        f"/api/v1/workspace-decisions/{replacement.json()['id']}/accept",
        json={"expected_version": 1},
    )
    assert accepted_replacement.status_code == 200, accepted_replacement.text

    decisions = client.get(f"/api/v1/workspaces/{workspace['id']}/decisions")
    assert decisions.status_code == 200
    by_id = {item["id"]: item for item in decisions.json()}
    assert by_id[first["id"]]["status"] == "SUPERSEDED"
    assert by_id[replacement.json()["id"]]["status"] == "ACCEPTED"
    assert by_id[replacement.json()["id"]]["supersedes_decision_id"] == first["id"]

    first_events = client.get(f"/api/v1/workspace-decisions/{first['id']}/events").json()
    assert [item["name"] for item in first_events] == [
        "workspace_decision.proposed",
        "workspace_decision.revised",
        "workspace_decision.accepted",
        "workspace_decision.superseded",
    ]


def test_collaboration_records_enforce_workspace_access(client: TestClient) -> None:
    user_response = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "decision-reader",
            "email": "decision-reader@obsion.dev",
            "display_name": "Decision Reader",
            "attributes": {},
        },
    )
    assert user_response.status_code == 201, user_response.text
    user_id = user_response.json()["id"]
    workspace = _workspace(client, "Private coordination")
    task = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        json={"title": "Private task"},
    ).json()
    decision = client.post(
        f"/api/v1/workspaces/{workspace['id']}/decisions",
        json={
            "title": "Private decision",
            "summary": "Restricted to workspace members.",
            "rationale": "The workspace is private.",
        },
    ).json()

    reader = Principal(
        id=UUID(user_id),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="decision-reader",
        display_name="Decision Reader",
    )
    client.app.dependency_overrides[get_principal] = lambda: reader
    try:
        assert client.get(f"/api/v1/workspaces/{workspace['id']}/tasks").status_code == 404
        assert client.get(f"/api/v1/workspace-decisions/{decision['id']}/events").status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    member = client.put(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"user_id": user_id, "permissions": ["read"]},
    )
    assert member.status_code == 200, member.text
    client.app.dependency_overrides[get_principal] = lambda: reader
    try:
        assert client.get(f"/api/v1/workspaces/{workspace['id']}/tasks").status_code == 200
        assert client.get(f"/api/v1/workspaces/{workspace['id']}/decisions").status_code == 200
        denied = client.patch(
            f"/api/v1/workspace-tasks/{task['id']}",
            json={"expected_version": 1, "status": "IN_PROGRESS"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "workspace_write_denied"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    other_tenant = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-reader",
        display_name="Cross-tenant Reader",
        permissions=frozenset({"workspace.read.all", "workspace.manage.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other_tenant
    try:
        assert client.get(f"/api/v1/workspace-tasks/{task['id']}/events").status_code == 404
        assert (
            client.get(f"/api/v1/workspace-decisions/{decision['id']}/versions").status_code == 404
        )
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
