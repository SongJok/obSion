from pathlib import Path

import pytest

from obsion.registry.agent_spec import ALLOWED_SANDBOX_MOUNTS, AgentSpec
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
  description: Research agent
  modelPolicy: {profile: reasoning-high}
  maxSteps: 9
  timeout: 120
  skills: [research]
  riskPolicy: {maxLevel: L1}
  capabilities: [knowledge.search]
  memory: {session: true}
  sandbox: {enabled: true, network: gateway-only}
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
            "description": "Research agent",
            "modelPolicy": {"profile": "reasoning-high"},
            "maxSteps": 9,
            "timeout": 120,
            "skills": ["research"],
            "riskPolicy": {"maxLevel": "L1"},
            "capabilities": ["knowledge.search"],
            "memory": {"session": True},
            "sandbox": {"enabled": True, "network": "gateway-only"},
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


def test_agent_spec_binds_model_profile_not_provider_details() -> None:
    parsed = AgentSpec.from_dict(
        {
            "description": "Primary coordinator",
            "modelPolicy": {"profile": "reasoning-high"},
            "maxSteps": 30,
            "timeout": 300,
            "skills": ["knowledge-research"],
            "capabilities": ["knowledge.search"],
            "riskPolicy": {"maxLevel": "L2"},
            "memory": {"session": True, "workspace": True},
            "sandbox": {"enabled": True, "network": "gateway-only"},
        }
    )

    assert parsed.model_profile == "reasoning-high"
    assert parsed.max_steps == 30
    assert parsed.timeout_seconds == 300
    assert parsed.capabilities == ("knowledge.search",)

    missing_sandbox = AgentSpec.from_dict(
        {
            "description": "Legacy coordinator",
            "modelPolicy": {"profile": "reasoning-high"},
            "maxSteps": 8,
            "capabilities": ["knowledge.search"],
            "riskPolicy": {"maxLevel": "L1"},
        }
    )
    assert missing_sandbox.sandbox["network"] == "gateway-only"
    assert missing_sandbox.sandbox["enabled"] is True
    assert missing_sandbox.sandbox["mounts"] == list(ALLOWED_SANDBOX_MOUNTS)

    with pytest.raises(RegistryManifestError, match="ModelProfile"):
        AgentSpec.from_dict(
            {
                "description": "Unsafe coordinator",
                "modelPolicy": {"profile": "reasoning-high", "model": "vendor-model"},
                "maxSteps": 30,
                "capabilities": ["knowledge.search"],
                "riskPolicy": {"maxLevel": "L2"},
            }
        )


@pytest.mark.parametrize(
    "unsafe_fragment",
    [
        {"databaseUrl": "postgresql://agent:password@production/db"},
        {"memory": {"dsn": "mysql://production/app"}},
        {"sandbox": {"network": "gateway-only", "token": "inline-secret"}},
        {"description": "Connect with password=production-secret"},
    ],
)
def test_agent_spec_rejects_direct_database_and_credential_configuration(
    unsafe_fragment: dict,
) -> None:
    spec = {
        "description": "Safe coordinator",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 30,
        "capabilities": ["knowledge.search"],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": {"network": "gateway-only"},
    }
    spec.update(unsafe_fragment)

    with pytest.raises(RegistryManifestError, match="runtime connection|endpoint"):
        AgentSpec.from_dict(spec)
