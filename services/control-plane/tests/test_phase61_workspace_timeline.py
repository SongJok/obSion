from __future__ import annotations

import ast
import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run_id}")


def test_workspace_timeline_lists_persisted_run_events(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Timeline workspace", "description": "Harness events only"},
    )
    assert workspace.status_code == 201, workspace.text
    other = client.post(
        "/api/v1/workspaces",
        json={"name": "Empty timeline", "description": "Isolation"},
    ).json()
    empty = client.get(f"/api/v1/workspaces/{workspace.json()['id']}/timeline")
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Greeting"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    run_events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert run_events
    names = {item["name"] for item in run_events}
    assert {"intent.detected", "plan.created", "run.completed"} <= names

    listed = client.get(f"/api/v1/workspaces/{workspace.json()['id']}/timeline")
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()} == {item["id"] for item in run_events}
    assert all(item["run_id"] == run["id"] for item in listed.json())
    assert client.get(f"/api/v1/workspaces/{other['id']}/timeline").json() == []


def test_workspace_timeline_is_tenant_scoped(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Isolated timeline", "description": "Tenant boundary"},
    ).json()
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-timeline",
        display_name="Cross-tenant Timeline",
        permissions=frozenset({"workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        listed = client.get(f"/api/v1/workspaces/{workspace['id']}/timeline")
        assert listed.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_timeline_is_not_fabricated() -> None:
    source = (_SOURCE_ROOT / "persistence" / "events.py").read_text(encoding="utf-8")
    assert "async def list_workspace" in source
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    assert "kafka" not in source.casefold()
    assert "clickhouse" not in source.casefold()
    view = (WEB_ROOT / "src" / "components" / "timeline-view.tsx").read_text(encoding="utf-8")
    assert "运行时间线" in view
    assert "不伪造时间线" in view
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'id: "timeline"' in sidebar
