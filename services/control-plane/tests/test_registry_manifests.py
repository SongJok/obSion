from pathlib import Path

import pytest

from obsion.registry.manifests import (
    RegistryManifestError,
    load_registry_specs,
    validate_registry_root,
)


def test_declarative_registry_manifests_override_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "agents"
    skills = tmp_path / "skills"
    agents.mkdir()
    skills.mkdir()
    connectors = tmp_path / "connectors"
    connectors.mkdir()
    (agents / "research.yaml").write_text(
        """apiVersion: obsion.dev/v1
kind: Agent
metadata:
  name: research-agent
spec:
  maxSteps: 9
  riskPolicy: {maxLevel: L1}
  capabilities: [knowledge.search]
""",
        encoding="utf-8",
    )
    (skills / "research.yaml").write_text(
        """apiVersion: obsion.dev/v1
kind: Skill
metadata:
  name: research
spec:
  capabilities: [knowledge.search]
""",
        encoding="utf-8",
    )
    (connectors / "knowledge.yaml").write_text(
        """apiVersion: obsion.dev/v1
kind: Connector
metadata:
  name: knowledge
spec:
  type: knowledge-index
  environment: internal
  transport: INTERNAL
  grants: [knowledge.read]
  allowedEgress: []
  capabilities: [knowledge.search]
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OBSION_REGISTRY_ROOT", str(tmp_path))

    loaded_agents, loaded_skills = load_registry_specs({"fallback": {}}, {"fallback": {}})

    assert loaded_agents == {
        "research-agent": {
            "maxSteps": 9,
            "riskPolicy": {"maxLevel": "L1"},
            "capabilities": ["knowledge.search"],
        }
    }
    assert loaded_skills == {"research": {"capabilities": ["knowledge.search"]}}
    assert validate_registry_root(tmp_path) == (1, 1, 1)


def test_invalid_registry_manifest_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "invalid.yaml").write_text("kind: Agent\nmetadata: {}\n", encoding="utf-8")
    monkeypatch.setenv("OBSION_REGISTRY_ROOT", str(tmp_path))

    with pytest.raises(RegistryManifestError):
        load_registry_specs({}, {})
