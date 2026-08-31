"""Connector plugin supply-chain gate.

goal.txt requires Develop → Security Scan → Signature → Registry → Approval →
Production, with Network / Filesystem / Capabilities / Secrets / Risk declarations.

This module is a static manifest policy. It does not scan binaries, verify GPG or
cosign, pip-install, importlib, or fetch a remote plugin. HMAC-SHA256 over the
canonical declaration is the V1 signature. Production without a verifiable
signature fails closed. L5 is never registered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from obsion.common.errors import AuthorizationError, ObsionError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus, RiskLevel
from obsion.security.identity import Principal
from obsion.telemetry import connector_plugin_counter
from obsion_sdk.connector import (
    DEVELOPMENT_PLUGIN,
    ConnectorPluginDeclaration,
    ConnectorSdkError,
    DevelopmentEchoConnector,
    parse_plugin_declaration,
    plugin_declaration_digest,
    verify_plugin_signature,
)

SPI_CONNECTOR_TYPES = frozenset({DevelopmentEchoConnector.CONNECTOR_TYPE})
MANIFEST_KEY_ENV = "OBSION_CONNECTOR_MANIFEST_KEY"
MISSING_PLUGIN_MESSAGE = (
    "Connector SDK plugins must declare network, filesystem, capabilities, secrets, and risk"
)
UNSIGNED_PRODUCTION_MESSAGE = (
    "Production connector plugins require a verified HMAC-SHA256 signature"
)
APPROVAL_REQUIRED_MESSAGE = "L3+ connector plugins require an approval decision before promotion"
L5_DENIED_MESSAGE = "L5 connector plugins are denied"
IN_PROCESS_FILESYSTEM_MESSAGE = "In-process Connector SDK plugins cannot declare filesystem mounts"
PLUGIN_CAPABILITY_MESSAGE = "Plugin capabilities must be a subset of the connector capability set"

_VALIDATION_CODES = frozenset(
    {
        "capability_input_invalid",
        "connector_egress_denied",
        "connector_grant_missing",
        "inline_secret_denied",
        "v1_production_action_boundary",
    }
)


@dataclass(frozen=True, slots=True)
class PluginScanResult:
    status: str
    checked_at: str
    risk: str | None
    network: str | None
    filesystem: tuple[str, ...]
    secrets: tuple[str, ...]
    capabilities: tuple[str, ...]
    signature: str
    lifecycle: str
    declaration_sha256: str | None
    findings: tuple[str, ...]
    error_code: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "checked_at": self.checked_at,
            "risk": self.risk,
            "network": self.network,
            "filesystem": list(self.filesystem),
            "secrets": list(self.secrets),
            "capabilities": list(self.capabilities),
            "signature": self.signature,
            "lifecycle": self.lifecycle,
            "declaration_sha256": self.declaration_sha256,
            "findings": list(self.findings),
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


def is_spi_connector(connector: Connector) -> bool:
    return connector.connector_type in SPI_CONNECTOR_TYPES


def inspect_plugin(connector: Connector) -> PluginScanResult:
    checked_at = utc_now().isoformat()
    if not is_spi_connector(connector):
        return PluginScanResult(
            status="not_applicable",
            checked_at=checked_at,
            risk=None,
            network=None,
            filesystem=(),
            secrets=(),
            capabilities=(),
            signature="not_required",
            lifecycle="registered",
            declaration_sha256=None,
            findings=(),
            error_code=None,
            message="First-party connectors are not Connector SDK plugins",
        )
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    raw = configuration.get("plugin")
    if raw is None:
        return _failed(
            checked_at,
            code="capability_transport_unavailable",
            message=MISSING_PLUGIN_MESSAGE,
            signature="missing",
        )
    if not isinstance(raw, dict):
        return _failed(
            checked_at,
            code="capability_input_invalid",
            message="Plugin declaration must be an object",
            signature="missing",
        )
    try:
        declaration = parse_plugin_declaration(raw)
    except ConnectorSdkError as exc:
        code = exc.code
        if "env://" in exc.message or "secret://" in exc.message:
            code = "inline_secret_denied"
        if "filesystem mounts" in exc.message:
            code = "capability_input_invalid"
        if "network must be" in exc.message:
            code = "connector_egress_denied"
        if "secrets must be" in exc.message:
            code = "inline_secret_denied"
        return _failed(
            checked_at,
            code=code,
            message=exc.message,
            signature="missing",
        )
    signature_status, signature_error = _signature_status(connector, declaration)
    findings: list[str] = []
    error_code: str | None = None
    message = "Plugin scan passed"

    if declaration.risk == "L5":
        findings.append(L5_DENIED_MESSAGE)
        error_code = "v1_production_action_boundary"
        message = L5_DENIED_MESSAGE
    if declaration.network not in {"deny", "gateway-only"}:
        findings.append("Plugin network must be deny or gateway-only")
        error_code = error_code or "connector_egress_denied"
        message = findings[-1]
    if connector.allowed_egress:
        findings.append("Connector SDK plugins cannot declare egress")
        error_code = error_code or "connector_egress_denied"
        message = findings[-1]
    if declaration.filesystem:
        findings.append(IN_PROCESS_FILESYSTEM_MESSAGE)
        error_code = error_code or "capability_input_invalid"
        message = IN_PROCESS_FILESYSTEM_MESSAGE
    advertised = _advertised_capabilities(connector)
    if advertised and any(item not in advertised for item in declaration.capabilities):
        findings.append(PLUGIN_CAPABILITY_MESSAGE)
        error_code = error_code or "connector_grant_missing"
        message = PLUGIN_CAPABILITY_MESSAGE
    if (
        connector.credential_ref
        and declaration.secrets
        and connector.credential_ref not in declaration.secrets
    ):
        findings.append("Connector credential_ref must be listed in plugin secrets")
        error_code = error_code or "inline_secret_denied"
        message = findings[-1]
    if signature_error:
        findings.append(signature_error)
        error_code = error_code or "capability_transport_unavailable"
        message = signature_error

    status = "failed" if error_code else "passed"
    lifecycle = _lifecycle(connector, status, signature_status, declaration.risk)
    result = PluginScanResult(
        status=status,
        checked_at=checked_at,
        risk=declaration.risk,
        network=declaration.network,
        filesystem=declaration.filesystem,
        secrets=declaration.secrets,
        capabilities=declaration.capabilities,
        signature=signature_status,
        lifecycle=lifecycle,
        declaration_sha256=plugin_declaration_digest(declaration),
        findings=tuple(findings),
        error_code=error_code,
        message=message,
    )
    connector_plugin_counter.add(1, {"operation": "scan", "status": status})
    return result


def enforce_plugin_governance(connector: Connector) -> PluginScanResult:
    result = inspect_plugin(connector)
    if result.status == "not_applicable":
        return result
    if result.status != "passed":
        _raise_scan_failure(result)
    if plugin_requires_approval(result.risk) and connector.status != ConnectorStatus.ACTIVE:
        raise ObsionError(
            "capability_denied",
            "L3+ connector plugins cannot execute before promotion",
        )
    return result


def promote_plugin(connector: Connector, principal: Principal) -> PluginScanResult:
    result = inspect_plugin(connector)
    if result.status != "passed":
        _raise_scan_failure(result)
    if plugin_requires_approval(result.risk) and not principal.can("approval.decide"):
        raise AuthorizationError("approval_decide_denied", APPROVAL_REQUIRED_MESSAGE)
    if connector.environment == "production" and result.signature != "verified":
        raise ObsionError("capability_transport_unavailable", UNSIGNED_PRODUCTION_MESSAGE)
    connector.status = ConnectorStatus.ACTIVE
    promoted = inspect_plugin(connector)
    connector_plugin_counter.add(1, {"operation": "promote", "status": promoted.status})
    return promoted


def merge_scan_into_health(
    health: dict[str, Any],
    scan: PluginScanResult | dict[str, Any],
) -> dict[str, Any]:
    payload = dict(health)
    payload["scan"] = scan.as_dict() if isinstance(scan, PluginScanResult) else dict(scan)
    return payload


def development_plugin_configuration(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    configuration = {
        "operation": DevelopmentEchoConnector.OPERATION,
        "plugin": dict(DEVELOPMENT_PLUGIN),
    }
    if extra:
        configuration.update(extra)
    return configuration


def validate_manifest_plugin(spec: dict[str, Any], filename: str) -> None:
    if spec.get("type") not in SPI_CONNECTOR_TYPES:
        if spec.get("plugin") is not None:
            raise ValueError(
                f"Connector manifest {filename} cannot declare plugin unless it is "
                "a Connector SDK type"
            )
        return
    plugin = spec.get("plugin")
    configuration = spec.get("configuration")
    nested = configuration.get("plugin") if isinstance(configuration, dict) else None
    if plugin is None:
        plugin = nested
    elif nested is not None and nested != plugin:
        raise ValueError(f"Connector manifest {filename} plugin must match configuration.plugin")
    if plugin is None:
        raise ValueError(f"Connector manifest {filename} requires spec.plugin")
    if not isinstance(plugin, dict):
        raise ValueError(f"Connector manifest {filename} plugin must be an object")
    try:
        declaration = parse_plugin_declaration(plugin)
    except ConnectorSdkError as exc:
        raise ValueError(f"Connector manifest {filename} {exc.message}") from exc
    if declaration.risk == "L5":
        raise ValueError(f"Connector manifest {filename} {L5_DENIED_MESSAGE}")
    if declaration.filesystem:
        raise ValueError(f"Connector manifest {filename} {IN_PROCESS_FILESYSTEM_MESSAGE}")
    advertised = spec.get("capabilities")
    undeclared = isinstance(advertised, list) and any(
        item not in advertised for item in declaration.capabilities
    )
    if undeclared:
        raise ValueError(f"Connector manifest {filename} {PLUGIN_CAPABILITY_MESSAGE}")
    environment = spec.get("environment")
    signature = spec.get("signature")
    if signature is None and isinstance(configuration, dict):
        signature = configuration.get("plugin_signature")
    if environment == "production" and not (isinstance(signature, str) and signature.strip()):
        raise ValueError(f"Connector manifest {filename} {UNSIGNED_PRODUCTION_MESSAGE}")


def _advertised_capabilities(connector: Connector) -> frozenset[str]:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    advertised = configuration.get("capabilities")
    if isinstance(advertised, list) and advertised:
        return frozenset(str(item) for item in advertised if isinstance(item, str))
    return frozenset({DevelopmentEchoConnector.CAPABILITY})


def _signature_status(
    connector: Connector,
    declaration: ConnectorPluginDeclaration,
) -> tuple[str, str | None]:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    signature = configuration.get("plugin_signature")
    key = os.environ.get(MANIFEST_KEY_ENV)
    production = connector.environment == "production"
    if signature is not None and (not isinstance(signature, str) or not signature.strip()):
        return "invalid", "Plugin signature must be an HMAC-SHA256 hex digest"
    if not signature:
        if production:
            return "missing", UNSIGNED_PRODUCTION_MESSAGE
        return "not_required", None
    if not key or not key.strip():
        if production:
            return "missing", UNSIGNED_PRODUCTION_MESSAGE
        return "unverified", None
    try:
        valid = verify_plugin_signature(declaration, signature, key)
    except ConnectorSdkError:
        return "invalid", "Plugin signature cannot be verified"
    if valid:
        return "verified", None
    return "invalid", "Plugin signature does not match the declaration"


def _lifecycle(connector: Connector, status: str, signature: str, risk: str | None) -> str:
    if status != "passed":
        return "develop"
    if connector.environment == "production" and signature != "verified":
        return "scanned"
    if plugin_requires_approval(risk) and connector.status != ConnectorStatus.ACTIVE:
        return "signed"
    if plugin_requires_approval(risk) and connector.status == ConnectorStatus.ACTIVE:
        return "production" if connector.environment == "production" else "approved"
    if connector.status != ConnectorStatus.ACTIVE:
        return "registered"
    return "production" if connector.environment == "production" else "registered"


def plugin_requires_approval(risk: str | None) -> bool:
    if risk is None:
        return False
    try:
        return RiskLevel(risk).ordinal >= 3
    except ValueError:
        return True


def _failed(
    checked_at: str,
    *,
    code: str,
    message: str,
    signature: str,
) -> PluginScanResult:
    connector_plugin_counter.add(1, {"operation": "scan", "status": "failed"})
    return PluginScanResult(
        status="failed",
        checked_at=checked_at,
        risk=None,
        network=None,
        filesystem=(),
        secrets=(),
        capabilities=(),
        signature=signature,
        lifecycle="develop",
        declaration_sha256=None,
        findings=(message,),
        error_code=code,
        message=message,
    )


def _raise_scan_failure(result: PluginScanResult) -> None:
    code = result.error_code or "capability_transport_unavailable"
    message = result.message
    if code == "capability_transport_unavailable":
        raise ObsionError("capability_transport_unavailable", message)
    if code == "capability_input_invalid":
        raise ValidationError("capability_input_invalid", message)
    if code == "connector_egress_denied":
        raise ValidationError("connector_egress_denied", message)
    if code == "connector_grant_missing":
        raise ValidationError("connector_grant_missing", message)
    if code == "inline_secret_denied":
        raise ValidationError("inline_secret_denied", message)
    if code == "v1_production_action_boundary":
        raise ValidationError("v1_production_action_boundary", message)
    if code in _VALIDATION_CODES:
        raise ValidationError("capability_input_invalid", message)
    raise ObsionError("capability_transport_unavailable", message)
