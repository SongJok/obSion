import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from obsion.registry.manifests import RegistryManifestError

_SUPPORTED_RISK_LEVELS = frozenset({"L0", "L1", "L2"})
_FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "accesskey",
        "apikey",
        "baseurl",
        "connectionstring",
        "credential",
        "credentials",
        "database",
        "databaseurl",
        "dsn",
        "endpoint",
        "jdbcurl",
        "modelid",
        "password",
        "passwd",
        "privatekey",
        "provider",
        "secret",
        "token",
    }
)
_FORBIDDEN_RUNTIME_VALUE = re.compile(
    r"(?i)(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|jdbc):(?:/{2})?"
    r"|(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\s*[:=]"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

ALLOWED_SANDBOX_NETWORKS = frozenset({"deny", "gateway-only"})
ALLOWED_SANDBOX_MOUNTS = ("/workspace", "/repo", "/artifacts", "/tmp")  # noqa: S108
_ALLOWED_SANDBOX_KEYS = frozenset(
    {
        "enabled",
        "network",
        "mounts",
        "cpuMillis",
        "memoryMb",
        "diskMb",
        "processLimit",
    }
)
_SANDBOX_INT_BOUNDS = {
    "cpuMillis": (1, 64_000),
    "memoryMb": (32, 65_536),
    "diskMb": (32, 1_048_576),
    "processLimit": (1, 4_096),
}


def sandbox_allows_capabilities(sandbox: Mapping[str, Any]) -> bool:
    """Capability Gateway is forbidden when AgentSpec pins network deny."""
    return sandbox.get("network") != "deny"


def normalize_sandbox(value: Any, *, source: str) -> dict[str, Any]:
    """Normalize AgentSpec sandbox. OS isolation is not claimed here."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise RegistryManifestError(f"{source} has an invalid sandbox policy")
    unknown = sorted(set(value) - _ALLOWED_SANDBOX_KEYS)
    if unknown:
        raise RegistryManifestError(f"{source} sandbox cannot declare {unknown[0]!r}")
    enabled = value.get("enabled", True)
    if enabled is not True:
        raise RegistryManifestError(f"{source} sandbox.enabled must be true")
    network = value.get("network")
    if network is None:
        network = "gateway-only"
    elif network not in ALLOWED_SANDBOX_NETWORKS:
        raise RegistryManifestError(f"{source} sandbox.network must be deny or gateway-only")
    mounts = value.get("mounts")
    if mounts is None:
        mounts = list(ALLOWED_SANDBOX_MOUNTS)
    elif not isinstance(mounts, list) or not mounts:
        raise RegistryManifestError(f"{source} sandbox.mounts must be a non-empty list")
    else:
        normalized_mounts: list[str] = []
        for item in mounts:
            if not isinstance(item, str) or item not in ALLOWED_SANDBOX_MOUNTS:
                raise RegistryManifestError(
                    f"{source} sandbox.mounts may only include "
                    "/workspace, /repo, /artifacts, and /tmp"
                )
            if item in normalized_mounts:
                raise RegistryManifestError(f"{source} sandbox.mounts has duplicates")
            normalized_mounts.append(item)
        mounts = normalized_mounts
    normalized: dict[str, Any] = {
        "enabled": True,
        "network": network,
        "mounts": mounts,
    }
    for key, (lo, hi) in _SANDBOX_INT_BOUNDS.items():
        if key not in value:
            continue
        raw = value[key]
        if not isinstance(raw, int) or isinstance(raw, bool) or not lo <= raw <= hi:
            raise RegistryManifestError(f"{source} sandbox.{key} is out of range")
        normalized[key] = raw
    return normalized


def validate_model_context_configuration(value: Any, *, source: str) -> None:
    """Reject direct runtime connections and secrets in Agent/Skill context."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _FORBIDDEN_RUNTIME_KEYS:
                raise RegistryManifestError(
                    f"{source} cannot contain runtime connection or credential field {key!r}"
                )
            validate_model_context_configuration(item, source=source)
        return
    if isinstance(value, list):
        for item in value:
            validate_model_context_configuration(item, source=source)
        return
    if isinstance(value, str) and _FORBIDDEN_RUNTIME_VALUE.search(value):
        raise RegistryManifestError(
            f"{source} cannot contain an endpoint, database DSN, or credential value"
        )


@dataclass(frozen=True, slots=True)
class AgentSpec:
    description: str
    model_profile: str
    max_steps: int
    timeout_seconds: int
    skills: tuple[str, ...]
    prompts: tuple[str, ...]
    capabilities: tuple[str, ...]
    risk_max_level: str
    memory: dict[str, Any]
    sandbox: dict[str, Any]

    @classmethod
    def from_dict(cls, spec: dict[str, Any], *, source: str = "AgentSpec") -> "AgentSpec":
        validate_model_context_configuration(spec, source=source)
        description = _string(spec, "description", source)
        model_policy = spec.get("modelPolicy")
        if not isinstance(model_policy, dict):
            raise RegistryManifestError(f"{source} requires modelPolicy")
        model_profile = _string(model_policy, "profile", source)
        forbidden_model_fields = ("provider", "model", "model" + "_" + "id", "api_key")
        if any(field in model_policy for field in forbidden_model_fields):
            raise RegistryManifestError(
                f"{source} must bind a ModelProfile, not provider credentials or model IDs"
            )

        max_steps = spec.get("maxSteps")
        if (
            not isinstance(max_steps, int)
            or isinstance(max_steps, bool)
            or not 1 <= max_steps <= 200
        ):
            raise RegistryManifestError(f"{source} has an invalid maxSteps")
        timeout = spec.get("timeout", 300)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
            raise RegistryManifestError(f"{source} has an invalid timeout")

        capabilities = _string_tuple(spec, "capabilities", source, required=True)
        skills = _string_tuple(spec, "skills", source, required=False)
        prompts = _string_tuple(spec, "prompts", source, required=False)
        risk_policy = spec.get("riskPolicy")
        if not isinstance(risk_policy, dict):
            raise RegistryManifestError(f"{source} requires riskPolicy")
        risk_max_level = _string(risk_policy, "maxLevel", source)
        if risk_max_level not in _SUPPORTED_RISK_LEVELS:
            raise RegistryManifestError(f"{source} must declare a V1 riskPolicy.maxLevel")

        memory = spec.get("memory", {})
        if not isinstance(memory, dict):
            raise RegistryManifestError(f"{source} has an invalid memory policy")
        sandbox = normalize_sandbox(spec.get("sandbox"), source=source)

        return cls(
            description=description,
            model_profile=model_profile,
            max_steps=max_steps,
            timeout_seconds=timeout,
            skills=skills,
            prompts=prompts,
            capabilities=capabilities,
            risk_max_level=risk_max_level,
            memory=dict(memory),
            sandbox=dict(sandbox),
        )


def _string(spec: dict[str, Any], key: str, source: str) -> str:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryManifestError(f"{source} requires non-empty {key}")
    return value.strip()


def _string_tuple(
    spec: dict[str, Any],
    key: str,
    source: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    value = spec.get(key, [])
    if not isinstance(value, list) or (required and not value):
        raise RegistryManifestError(f"{source} requires string {key}")
    normalized = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(normalized) != len(value) or len(set(normalized)) != len(normalized):
        raise RegistryManifestError(f"{source} has invalid duplicate {key}")
    return normalized
