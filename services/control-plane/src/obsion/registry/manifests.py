import os
import re
from pathlib import Path
from typing import Any

import yaml


class RegistryManifestError(ValueError):
    """Raised when a declarative registry manifest cannot be trusted."""


def load_registry_specs(
    fallback_agents: dict[str, dict[str, Any]],
    fallback_skills: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    root = Path(os.environ.get("OBSION_REGISTRY_ROOT", Path.cwd())).resolve()
    agents = _load_kind(root / "agents", "Agent")
    skills = _load_kind(root / "skills", "Skill")
    return agents or fallback_agents, skills or fallback_skills


def validate_registry_root(root: Path) -> tuple[int, int, int]:
    resolved = root.resolve()
    agents = _load_kind(resolved / "agents", "Agent")
    skills = _load_kind(resolved / "skills", "Skill")
    connectors = _load_kind(resolved / "connectors", "Connector")
    if not agents or not skills or not connectors:
        raise RegistryManifestError(
            "Registry root must contain Agent, Skill, and Connector manifests"
        )
    return len(agents), len(skills), len(connectors)


def _load_kind(directory: Path, expected_kind: str) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        return {}
    loaded: dict[str, dict[str, Any]] = {}
    for path in sorted((*directory.rglob("*.yaml"), *directory.rglob("*.yml"))):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RegistryManifestError(f"Unable to load registry manifest {path.name}") from exc
        if not isinstance(document, dict):
            raise RegistryManifestError(f"Registry manifest {path.name} must be an object")
        if document.get("apiVersion") != "obsion.dev/v1" or document.get("kind") != expected_kind:
            raise RegistryManifestError(
                f"Registry manifest {path.name} must be obsion.dev/v1 {expected_kind}"
            )
        metadata = document.get("metadata")
        spec = document.get("spec")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
            raise RegistryManifestError(f"Registry manifest {path.name} requires metadata.name")
        if not isinstance(spec, dict):
            raise RegistryManifestError(f"Registry manifest {path.name} requires an object spec")
        _validate_spec(expected_kind, spec, path.name)
        name = metadata["name"].strip()
        if not name or name in loaded:
            raise RegistryManifestError(
                f"Registry manifest {path.name} has an invalid duplicate name"
            )
        loaded[name] = spec
    return loaded


def _validate_spec(kind: str, spec: dict[str, Any], filename: str) -> None:
    capabilities = spec.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(item, str) and item.strip() for item in capabilities)
    ):
        raise RegistryManifestError(
            f"Registry manifest {filename} requires non-empty string capabilities"
        )
    if kind == "Agent":
        from obsion.registry.agent_spec import AgentSpec

        AgentSpec.from_dict(spec, source=f"Agent manifest {filename}")
    if kind == "Skill":
        from obsion.registry.agent_spec import validate_model_context_configuration

        validate_model_context_configuration(spec, source=f"Skill manifest {filename}")
    if kind == "Connector":
        required_strings = ("type", "environment", "transport")
        if any(
            not isinstance(spec.get(field), str) or not spec[field].strip()
            for field in required_strings
        ):
            raise RegistryManifestError(
                f"Connector manifest {filename} requires type, environment, and transport"
            )
        if spec["transport"] not in {
            "HTTP",
            "MCP",
            "SDK",
            "SQL_PROXY",
            "AGENT",
            "WORKFLOW",
            "INTERNAL",
        }:
            raise RegistryManifestError(f"Connector manifest {filename} has invalid transport")
        grants = spec.get("grants")
        egress = spec.get("allowedEgress")
        if not isinstance(grants, list) or not all(isinstance(item, str) for item in grants):
            raise RegistryManifestError(f"Connector manifest {filename} has invalid grants")
        if not isinstance(egress, list) or not all(isinstance(item, str) for item in egress):
            raise RegistryManifestError(f"Connector manifest {filename} has invalid allowedEgress")
        reference = spec.get("credentialRef")
        if reference is not None and (
            not isinstance(reference, str)
            or re.fullmatch(r"(?:env|secret)://[A-Za-z][A-Za-z0-9_.-]*", reference) is None
        ):
            raise RegistryManifestError(
                f"Connector manifest {filename} has an unsafe credentialRef"
            )
