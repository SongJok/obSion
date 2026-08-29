import re
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
        risk_policy = spec.get("riskPolicy")
        if not isinstance(risk_policy, dict):
            raise RegistryManifestError(f"{source} requires riskPolicy")
        risk_max_level = _string(risk_policy, "maxLevel", source)
        if risk_max_level not in _SUPPORTED_RISK_LEVELS:
            raise RegistryManifestError(f"{source} must declare a V1 riskPolicy.maxLevel")

        memory = spec.get("memory", {})
        if not isinstance(memory, dict):
            raise RegistryManifestError(f"{source} has an invalid memory policy")
        sandbox = spec.get("sandbox", {})
        if not isinstance(sandbox, dict):
            raise RegistryManifestError(f"{source} has an invalid sandbox policy")
        if sandbox and sandbox.get("network") not in {None, "deny", "gateway-only"}:
            raise RegistryManifestError(f"{source} sandbox.network must be deny or gateway-only")

        return cls(
            description=description,
            model_profile=model_profile,
            max_steps=max_steps,
            timeout_seconds=timeout,
            skills=skills,
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
