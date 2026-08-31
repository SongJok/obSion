from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from obsion.application.studio import mapping_diff

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_FORBIDDEN_STUDIO_IMPORTS = (
    "obsion.harness",
    "obsion.capabilities.gateway",
    "obsion.models",
    "obsion.application.evaluations",
)

_AGENT_V1 = """
apiVersion: obsion.dev/v1
kind: Agent
metadata:
  name: studio-version-agent
spec:
  description: Version one
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

_AGENT_V2 = _AGENT_V1.replace("Version one", "Version two").replace("maxSteps: 8", "maxSteps: 10")


def test_mapping_diff_redacts_secret_paths() -> None:
    changes = mapping_diff({"maxSteps": 8, "token": "secret"}, {"maxSteps": 10, "token": "other"})
    paths = {item["path"]: item for item in changes}
    assert paths["$.maxSteps"]["baseline"] == 8
    assert paths["$.maxSteps"]["candidate"] == 10
    assert paths["$.token"]["baseline"] == "[redacted]"
    assert paths["$.token"]["candidate"] == "[redacted]"


def test_studio_compare_and_rollback_do_not_rewrite_history(client: TestClient) -> None:
    first = client.post("/api/v1/studio/agents", json={"document": _AGENT_V1})
    assert first.status_code == 201, first.text
    assert first.json()["promoted"] is False
    promote = client.post(
        "/api/v1/studio/promote",
        json={"kind": "Agent", "name": "studio-version-agent", "version": 1},
    )
    assert promote.status_code == 200, promote.text
    second = client.post("/api/v1/studio/agents", json={"document": _AGENT_V2})
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    assert second.json()["promoted"] is False

    compared = client.post(
        "/api/v1/studio/compare",
        json={
            "kind": "Agent",
            "name": "studio-version-agent",
            "baseline_version": 1,
            "candidate_version": 2,
        },
    )
    assert compared.status_code == 200, compared.text
    body = compared.json()
    assert body["traffic_split"] is False
    assert body["identical"] is False
    assert any(item["path"] == "$.maxSteps" for item in body["changes"])
    assert "fixtures.actual" in body["evaluation"]

    same = client.post(
        "/api/v1/studio/compare",
        json={
            "kind": "Agent",
            "name": "studio-version-agent",
            "baseline_version": 1,
            "candidate_version": 1,
        },
    )
    assert same.status_code == 422
    assert same.json()["code"] == "registry_spec_invalid"

    client.post(
        "/api/v1/studio/promote",
        json={"kind": "Agent", "name": "studio-version-agent", "version": 2},
    )
    rollback = client.post(
        "/api/v1/studio/rollback",
        json={"kind": "Agent", "name": "studio-version-agent", "version": 1},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["promoted"] is True
    assert rollback.json()["version"] == 1
    catalog = client.get("/api/v1/studio/catalog")
    versions = [item for item in catalog.json()["agents"] if item["name"] == "studio-version-agent"]
    assert {item["version"] for item in versions} == {1, 2}
    assert next(item for item in versions if item["version"] == 1)["promoted"] is True
    assert next(item for item in versions if item["version"] == 2)["promoted"] is False
    assert next(item for item in versions if item["version"] == 2)["spec"]["maxSteps"] == 10


def test_prompt_compare_is_snapshot_only_and_rollback_is_denied(client: TestClient) -> None:
    first = client.post(
        "/api/v1/admin/prompts",
        json={
            "name": "studio-version-prompt",
            "display_name": "Studio prompt",
            "template": "Answer from authorized evidence.",
            "variables_schema": {"type": "object"},
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/admin/prompts",
        json={
            "name": "studio-version-prompt",
            "display_name": "Studio prompt",
            "template": "Cite DOCUMENT evidence only.",
            "variables_schema": {"type": "object"},
        },
    )
    assert second.status_code == 201, second.text
    compared = client.post(
        "/api/v1/studio/compare",
        json={
            "kind": "Prompt",
            "name": "studio-version-prompt",
            "baseline_version": 1,
            "candidate_version": 2,
        },
    )
    assert compared.status_code == 200, compared.text
    body = compared.json()
    assert body["traffic_split"] is False
    assert body["identical"] is False
    assert any("template" in item["path"] for item in body["changes"])
    denied = client.post(
        "/api/v1/studio/rollback",
        json={"kind": "Prompt", "name": "studio-version-prompt", "version": 1},
    )
    assert denied.status_code == 422
    assert denied.json()["code"] == "registry_spec_invalid"
    workflow = client.post(
        "/api/v1/studio/compare",
        json={
            "kind": "Workflow",
            "name": "example",
            "baseline_version": 1,
            "candidate_version": 2,
        },
    )
    assert workflow.status_code == 422


def test_versioning_does_not_split_runtime_traffic_or_rewrite_prompts() -> None:
    source = (_SOURCE_ROOT / "application" / "studio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    violations = [
        imported
        for imported in imports
        if imported in _FORBIDDEN_STUDIO_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_STUDIO_IMPORTS)
    ]
    assert violations == []
    assert '"traffic_split": False' in source
    assert "PROMPT_CUTOVER_MESSAGE" in source
    assert "PromptDefinition.active_version" not in source
    assert "fixtures.actual is rejected" in source
    web = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "web"
        / "src"
        / "components"
        / "studio-view.tsx"
    ).read_text(encoding="utf-8")
    assert "selectedAgent" not in web
    assert "agent-picker" not in web.casefold()
    assert "no traffic split" in web
