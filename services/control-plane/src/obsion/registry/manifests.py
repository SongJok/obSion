import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from obsion.capabilities.plugin_governance import validate_manifest_plugin

_SUPPORTED_KINDS = frozenset({"Agent", "Skill", "Workflow", "Connector"})


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


def parse_registry_text(raw: str, *, source: str = "Studio") -> tuple[str, str, dict[str, Any]]:
    if not raw.strip():
        raise RegistryManifestError(f"{source} document is required")
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RegistryManifestError(f"{source} is not valid YAML") from exc
    return parse_loaded_document(document, source=source)


def parse_loaded_document(
    document: Any,
    *,
    source: str,
    expected_kind: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    if not isinstance(document, dict):
        raise RegistryManifestError(f"Registry manifest {source} must be an object")
    kind = document.get("kind")
    if expected_kind is not None:
        if document.get("apiVersion") != "obsion.dev/v1" or kind != expected_kind:
            raise RegistryManifestError(
                f"Registry manifest {source} must be obsion.dev/v1 {expected_kind}"
            )
    elif document.get("apiVersion") != "obsion.dev/v1" or kind not in _SUPPORTED_KINDS:
        raise RegistryManifestError(
            f"Registry manifest {source} must be obsion.dev/v1 Agent, Skill, Workflow, or Connector"
        )
    metadata = document.get("metadata")
    spec = document.get("spec")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        raise RegistryManifestError(f"Registry manifest {source} requires metadata.name")
    if not isinstance(spec, dict):
        raise RegistryManifestError(f"Registry manifest {source} requires an object spec")
    _validate_spec(str(kind), spec, source)
    name = metadata["name"].strip()
    if not name:
        raise RegistryManifestError(f"Registry manifest {source} has an invalid duplicate name")
    return str(kind), name, spec


def _load_kind(directory: Path, expected_kind: str) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        return {}
    loaded: dict[str, dict[str, Any]] = {}
    for path in sorted((*directory.rglob("*.yaml"), *directory.rglob("*.yml"))):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RegistryManifestError(f"Unable to load registry manifest {path.name}") from exc
        _kind, name, spec = parse_loaded_document(
            document, source=path.name, expected_kind=expected_kind
        )
        if name in loaded:
            raise RegistryManifestError(
                f"Registry manifest {path.name} has an invalid duplicate name"
            )
        loaded[name] = spec
    return loaded


_AGENT_REMOTE_KEYS = frozenset(
    {
        "autogen",
        "child_run",
        "command",
        "crewai",
        "docker",
        "endpoint",
        "harness",
        "host",
        "hostname",
        "langchain",
        "langgraph",
        "nested",
        "agents_sdk",
        "sidecar",
        "spawn",
        "subprocess",
        "url",
    }
)


_MCP_REMOTE_KEYS = frozenset(
    {
        "args",
        "baseUrl",
        "base_url",
        "command",
        "cwd",
        "docker",
        "env",
        "http_url",
        "npx",
        "server",
        "sse",
        "stdio",
        "url",
    }
)


_GRPC_REMOTE_KEYS = frozenset(
    {
        "address",
        "authority",
        "cert",
        "certificate",
        "channel",
        "docker",
        "grpclib",
        "grpcio",
        "grpcurl",
        "host",
        "hostname",
        "insecure",
        "interceptors",
        "keepalive",
        "listen",
        "port",
        "proto",
        "protobuf",
        "protoc",
        "server",
        "socket",
        "ssl",
        "stub",
        "target",
        "tls",
        "unix",
        "uri",
        "url",
    }
)


_WORKFLOW_REMOTE_KEYS = frozenset(
    {
        "airflow",
        "camunda",
        "command",
        "cron",
        "dagster",
        "docker",
        "endpoint",
        "host",
        "hostname",
        "n8n",
        "prefect",
        "server",
        "temporal",
        "url",
        "webhook",
        "zeebe",
    }
)


_SDK_REMOTE_KEYS = frozenset(
    {
        "args",
        "baseUrl",
        "base_url",
        "class_name",
        "command",
        "entrypoint",
        "import",
        "module",
        "package",
        "pip",
        "url",
        "wheel",
    }
)


def _validate_in_process_connector(
    spec: dict[str, Any], filename: str, transport: str, remote_keys: frozenset[str]
) -> None:
    configuration = spec.get("configuration")
    config_keys = set(configuration) if isinstance(configuration, dict) else set()
    if (
        spec.get("baseUrl")
        or spec.get("endpoint")
        or spec.get("allowedEgress")
        or config_keys & remote_keys
    ):
        raise RegistryManifestError(
            f"Connector manifest {filename} {transport} transport cannot declare "
            "process spawn, remote URLs, package install, or egress"
        )


def _validate_spec(kind: str, spec: dict[str, Any], filename: str) -> None:
    if kind == "Workflow":
        from obsion.automation.schemas import WorkflowSpec

        try:
            WorkflowSpec.model_validate(spec)
        except PydanticValidationError as exc:
            raise RegistryManifestError(f"Workflow manifest {filename} is invalid") from exc
        return
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
            "GRPC",
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
        if spec["transport"] == "AGENT":
            _validate_in_process_connector(spec, filename, "AGENT", _AGENT_REMOTE_KEYS)
        if spec["transport"] == "GRPC":
            _validate_in_process_connector(spec, filename, "GRPC", _GRPC_REMOTE_KEYS)
        if spec["transport"] == "MCP":
            _validate_in_process_connector(spec, filename, "MCP", _MCP_REMOTE_KEYS)
        if spec["transport"] == "SDK":
            _validate_in_process_connector(spec, filename, "SDK", _SDK_REMOTE_KEYS)
        if spec["transport"] == "WORKFLOW":
            _validate_in_process_connector(spec, filename, "WORKFLOW", _WORKFLOW_REMOTE_KEYS)
        if spec.get("type") == "connector-sdk-development":
            _validate_in_process_connector(spec, filename, "INTERNAL", _SDK_REMOTE_KEYS)
            try:
                validate_manifest_plugin(spec, filename)
            except ValueError as exc:
                raise RegistryManifestError(str(exc)) from exc
