from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.domain.enums import SystemRole
from obsion.security.auth import get_principal
from obsion.security.identity import Principal
from obsion.security.roles import SYSTEM_ROLE_DEFINITIONS

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
STUDIO_SERVICE = (
    Path(__file__).resolve().parents[1] / "src" / "obsion" / "application" / "studio.py"
)

_AGENT_TEMPLATE = """
apiVersion: obsion.dev/v1
kind: Agent
metadata:
  name: studio-probe-agent
spec:
  description: Studio probe agent
  modelPolicy: {profile: reasoning-high}
  maxSteps: 8
  timeout: 120
  skills: []
  capabilities: [knowledge.search]
  riskPolicy: {maxLevel: L1}
  memory: {session: true}
  sandbox:
    enabled: true
    network: gateway-only
    mounts: [/workspace, /repo, /artifacts, /tmp]
"""

_SKILL_TEMPLATE = """
apiVersion: obsion.dev/v1
kind: Skill
metadata:
  name: studio-probe-skill
spec:
  instructions: [answer only from authorized DOCUMENT evidence]
  capabilities: [knowledge.search]
  requiredEvidence: [DOCUMENT]
  verification: [citation coverage]
"""

_WORKFLOW_TEMPLATE = """
apiVersion: obsion.dev/v1
kind: Workflow
metadata:
  name: studio-probe-workflow
spec:
  steps:
    - id: analyze
      name: Analyze
      type: ANALYSIS
      prompt: Summarize authorized evidence
"""


def _latest_promoted_agent(client: TestClient, name: str) -> dict:
    catalog = client.get("/api/v1/studio/catalog")
    assert catalog.status_code == 200, catalog.text
    return next(item for item in catalog.json()["agents"] if item["name"] == name)


def test_studio_is_not_a_second_harness_or_agent_picker() -> None:
    service = STUDIO_SERVICE.read_text(encoding="utf-8")
    assert "obsion.harness" not in service
    assert "CapabilityGateway" not in service
    assert "ModelGateway" not in service
    composer = (WEB_ROOT / "src" / "components" / "composer.tsx").read_text(encoding="utf-8")
    workbench = (WEB_ROOT / "src" / "components" / "workbench.tsx").read_text(encoding="utf-8")
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    studio = (WEB_ROOT / "src" / "components" / "studio-view.tsx").read_text(encoding="utf-8")
    types = (WEB_ROOT / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
    assert "selectedAgent" not in composer
    assert "agent-picker" not in composer.casefold()
    assert "Agent picker" not in workbench
    assert 'id: "studio"' in sidebar
    assert "StudioView" in workbench
    assert '| "studio"' in types
    assert "Agent picker" not in studio
    engineer = next(item for item in SYSTEM_ROLE_DEFINITIONS if item.name == SystemRole.ENGINEER)
    assert "registry.read" in engineer.permissions
    assert "registry.write" in engineer.permissions


def test_studio_validates_and_rejects_secrets(client: TestClient) -> None:
    valid = client.post("/api/v1/studio/validate", json={"document": _AGENT_TEMPLATE})
    assert valid.status_code == 200, valid.text
    body = valid.json()
    assert body["kind"] == "Agent"
    assert body["name"] == "studio-probe-agent"
    assert body["checksum_sha256"]
    assert "knowledge.search" in body["preview"]["capabilities"]

    leaked = client.post(
        "/api/v1/studio/validate",
        json={
            "document": _AGENT_TEMPLATE.replace(
                "modelPolicy: {profile: reasoning-high}",
                "modelPolicy: {profile: reasoning-high, api_key: sk-secret}",
            )
        },
    )
    assert leaked.status_code == 422, leaked.text
    assert leaked.json()["code"] == "registry_spec_invalid"

    secret_field = client.post(
        "/api/v1/studio/validate",
        json={
            "document": _AGENT_TEMPLATE.replace(
                "description: Studio probe agent",
                "description: Studio probe agent\n  secret: hunter2",
            )
        },
    )
    assert secret_field.status_code == 422, secret_field.text
    assert secret_field.json()["code"] == "registry_spec_invalid"

    workflow = client.post("/api/v1/studio/validate", json={"document": _WORKFLOW_TEMPLATE})
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["kind"] == "Workflow"
    assert workflow.json()["preview"]["steps"][0]["id"] == "analyze"


def test_studio_publishes_immutable_unpromoted_versions(client: TestClient) -> None:
    catalog = client.get("/api/v1/studio/catalog")
    assert catalog.status_code == 200, catalog.text
    general = next(item for item in catalog.json()["agents"] if item["name"] == "general-agent")
    assert general["promoted"] is True

    created = client.post("/api/v1/studio/agents", json={"document": _AGENT_TEMPLATE})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "studio-probe-agent"
    assert body["version"] == 1
    assert body["status"] == "DRAFT"
    assert body["promoted"] is False

    repeated = client.post("/api/v1/studio/agents", json={"document": _AGENT_TEMPLATE})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["version_id"] == body["version_id"]

    promoted = client.post(
        "/api/v1/studio/promote",
        json={"kind": "Agent", "name": "studio-probe-agent", "version": 1},
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["promoted"] is True
    assert promoted.json()["status"] == "ACTIVE"

    skill = client.post("/api/v1/studio/skills", json={"document": _SKILL_TEMPLATE})
    assert skill.status_code == 201, skill.text
    assert skill.json()["promoted"] is False
    skill_promoted = client.post(
        "/api/v1/studio/promote",
        json={"kind": "Skill", "name": "studio-probe-skill", "version": 1},
    )
    assert skill_promoted.status_code == 200, skill_promoted.text
    assert skill_promoted.json()["promoted"] is True

    workflow_publish = client.post("/api/v1/studio/agents", json={"document": _WORKFLOW_TEMPLATE})
    assert workflow_publish.status_code == 422, workflow_publish.text
    assert workflow_publish.json()["code"] == "registry_spec_invalid"


def test_unpublished_agent_versions_do_not_bind_new_turns(client: TestClient) -> None:
    current = _latest_promoted_agent(client, "general-agent")
    spec = dict(current["spec"])
    spec["maxSteps"] = int(spec["maxSteps"]) + 1
    document = json.dumps(
        {
            "apiVersion": "obsion.dev/v1",
            "kind": "Agent",
            "metadata": {"name": "general-agent"},
            "spec": spec,
        }
    )
    published = client.post("/api/v1/studio/agents", json={"document": document})
    assert published.status_code == 201, published.text
    assert published.json()["promoted"] is False
    assert published.json()["version"] == current["version"] + 1

    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Studio pin", "description": "Unpromoted versions must not bind"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Unpromoted pin"},
    )
    assert thread.status_code == 201, thread.text
    turn = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert turn.status_code == 202, turn.text
    assert turn.json()["run"]["agent_version_id"] == current["version_id"]

    promote = client.post(
        "/api/v1/studio/promote",
        json={"kind": "Agent", "name": "general-agent", "version": published.json()["version"]},
    )
    assert promote.status_code == 200, promote.text
    next_thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Promoted pin"},
    )
    assert next_thread.status_code == 201, next_thread.text
    next_turn = client.post(
        f"/api/v1/threads/{next_thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert next_turn.status_code == 202, next_turn.text
    assert next_turn.json()["run"]["agent_version_id"] == published.json()["version_id"]


def test_studio_denies_principals_without_registry_permissions(client: TestClient) -> None:
    viewer = Principal(
        id=UUID("00000000-0000-7000-8000-000000000002"),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="studio-viewer",
        display_name="Studio Viewer",
        permissions=frozenset(),
    )
    client.app.dependency_overrides[get_principal] = lambda: viewer
    try:
        denied = client.get("/api/v1/studio/catalog")
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "registry_read_denied"
        write_denied = client.post("/api/v1/studio/agents", json={"document": _AGENT_TEMPLATE})
        assert write_denied.status_code == 403, write_denied.text
        assert write_denied.json()["code"] == "registry_write_denied"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
