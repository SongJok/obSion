from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.plugin_governance import (
    IN_PROCESS_FILESYSTEM_MESSAGE,
    L5_DENIED_MESSAGE,
    MISSING_PLUGIN_MESSAGE,
    UNSIGNED_PRODUCTION_MESSAGE,
    development_plugin_configuration,
    enforce_plugin_governance,
    inspect_plugin,
    promote_plugin,
)
from obsion.common.errors import AuthorizationError, ObsionError, ValidationError
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus
from obsion.registry.manifests import RegistryManifestError, parse_loaded_document
from obsion.security.identity import Principal
from obsion_sdk.connector import (
    DEVELOPMENT_PLUGIN,
    DevelopmentEchoConnector,
    parse_plugin_declaration,
    sign_plugin_declaration,
)

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_FORBIDDEN_PLUGIN_IMPORTS = (
    "subprocess",
    "multiprocessing",
    "httpx",
    "requests",
    "aiohttp",
    "socket",
    "http.client",
    "urllib",
    "importlib",
    "pkgutil",
)
DEVELOPMENT_CONNECTOR_TYPE = DevelopmentEchoConnector.CONNECTOR_TYPE
DEVELOPMENT_CAPABILITY = DevelopmentEchoConnector.CAPABILITY


def _principal(**overrides: object) -> Principal:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "external_id": "phase47-user",
        "display_name": "Phase 47 User",
        "roles": frozenset({"admin"}),
        "permissions": frozenset({"connectors.write", "approval.decide", "admin.read"}),
    }
    values.update(overrides)
    return Principal(**values)  # type: ignore[arg-type]


def _connector(**overrides: object) -> Connector:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "obsion-connector-sdk-development",
        "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
        "status": ConnectorStatus.ACTIVE,
        "environment": "development",
        "configuration": development_plugin_configuration(),
        "declared_grants": ["connector.sdk.invoke"],
        "allowed_egress": [],
        "last_health": {"status": "ready"},
    }
    values.update(overrides)
    return Connector(**values)  # type: ignore[arg-type]


def test_development_plugin_scan_passes() -> None:
    result = inspect_plugin(_connector())
    assert result.status == "passed"
    assert result.risk == "L1"
    assert result.network == "deny"
    assert result.signature == "not_required"
    assert result.lifecycle == "registered"
    assert result.declaration_sha256
    enforce_plugin_governance(_connector())


def test_missing_plugin_and_l5_fail_closed() -> None:
    missing = inspect_plugin(_connector(configuration={"operation": "echo"}))
    assert missing.status == "failed"
    assert missing.error_code == "capability_transport_unavailable"
    assert MISSING_PLUGIN_MESSAGE in missing.findings
    with pytest.raises(ObsionError) as unavailable:
        enforce_plugin_governance(_connector(configuration={"operation": "echo"}))
    assert unavailable.value.code == "capability_transport_unavailable"

    l5 = dict(DEVELOPMENT_PLUGIN)
    l5["risk"] = "L5"
    blocked = inspect_plugin(_connector(configuration={"operation": "echo", "plugin": l5}))
    assert blocked.status == "failed"
    assert blocked.error_code == "v1_production_action_boundary"
    assert L5_DENIED_MESSAGE in blocked.findings
    with pytest.raises(ValidationError) as boundary:
        enforce_plugin_governance(_connector(configuration={"operation": "echo", "plugin": l5}))
    assert boundary.value.code == "v1_production_action_boundary"


def test_network_filesystem_and_inline_secrets_fail_closed() -> None:
    network = inspect_plugin(
        _connector(
            configuration={
                "operation": "echo",
                "plugin": {**DEVELOPMENT_PLUGIN, "network": "unrestricted"},
            }
        )
    )
    assert network.status == "failed"
    assert network.error_code == "connector_egress_denied"

    mounted = inspect_plugin(
        _connector(
            configuration={
                "operation": "echo",
                "plugin": {**DEVELOPMENT_PLUGIN, "filesystem": ["/workspace"]},
            }
        )
    )
    assert mounted.status == "failed"
    assert IN_PROCESS_FILESYSTEM_MESSAGE in mounted.findings

    secrets = inspect_plugin(
        _connector(
            configuration={
                "operation": "echo",
                "plugin": {**DEVELOPMENT_PLUGIN, "secrets": ["hunter2"]},
            }
        )
    )
    assert secrets.status == "failed"
    assert secrets.error_code == "inline_secret_denied"


def test_production_unsigned_plugin_fails_and_hmac_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsigned = inspect_plugin(_connector(environment="production"))
    assert unsigned.status == "failed"
    assert unsigned.signature == "missing"
    assert UNSIGNED_PRODUCTION_MESSAGE in unsigned.findings

    declaration = parse_plugin_declaration(DEVELOPMENT_PLUGIN)
    monkeypatch.setenv("OBSION_CONNECTOR_MANIFEST_KEY", "phase47-manifest-key")
    signature = sign_plugin_declaration(declaration, "phase47-manifest-key")
    signed = inspect_plugin(
        _connector(
            environment="production",
            configuration=development_plugin_configuration({"plugin_signature": signature}),
        )
    )
    assert signed.status == "passed"
    assert signed.signature == "verified"
    assert signed.lifecycle == "production"

    monkeypatch.setenv("OBSION_CONNECTOR_MANIFEST_KEY", "other-key")
    invalid = inspect_plugin(
        _connector(
            environment="production",
            configuration=development_plugin_configuration({"plugin_signature": signature}),
        )
    )
    assert invalid.status == "failed"
    assert invalid.signature == "invalid"


def test_l3_plugin_requires_promotion_and_approval() -> None:
    plugin = {**DEVELOPMENT_PLUGIN, "risk": "L3"}
    draft = _connector(
        status=ConnectorStatus.DRAFT,
        configuration={"operation": "echo", "plugin": plugin},
    )
    scan = inspect_plugin(draft)
    assert scan.status == "passed"
    assert scan.lifecycle == "signed"
    with pytest.raises(ObsionError) as denied:
        enforce_plugin_governance(draft)
    assert denied.value.code == "capability_denied"

    writer = _principal(permissions=frozenset({"connectors.write"}))
    with pytest.raises(AuthorizationError) as approval:
        promote_plugin(draft, writer)
    assert approval.value.code == "approval_decide_denied"

    promoted = promote_plugin(draft, _principal())
    assert draft.status == ConnectorStatus.ACTIVE
    assert promoted.lifecycle == "approved"
    enforce_plugin_governance(draft)


def test_first_party_connectors_are_not_plugins() -> None:
    result = inspect_plugin(_connector(connector_type="knowledge-index", configuration={}))
    assert result.status == "not_applicable"
    enforce_plugin_governance(_connector(connector_type="knowledge-index", configuration={}))


def test_manifest_requires_plugin_and_rejects_l5() -> None:
    with pytest.raises(RegistryManifestError, match="requires spec.plugin"):
        parse_loaded_document(
            {
                "apiVersion": "obsion.dev/v1",
                "kind": "Connector",
                "metadata": {"name": "missing-plugin"},
                "spec": {
                    "type": DEVELOPMENT_CONNECTOR_TYPE,
                    "environment": "development",
                    "transport": "INTERNAL",
                    "grants": ["connector.sdk.invoke"],
                    "allowedEgress": [],
                    "configuration": {"operation": "echo"},
                    "capabilities": [DEVELOPMENT_CAPABILITY],
                },
            },
            source="missing-plugin.yaml",
        )
    with pytest.raises(RegistryManifestError, match="L5"):
        parse_loaded_document(
            {
                "apiVersion": "obsion.dev/v1",
                "kind": "Connector",
                "metadata": {"name": "l5-plugin"},
                "spec": {
                    "type": DEVELOPMENT_CONNECTOR_TYPE,
                    "environment": "development",
                    "transport": "INTERNAL",
                    "grants": ["connector.sdk.invoke"],
                    "allowedEgress": [],
                    "plugin": {**DEVELOPMENT_PLUGIN, "risk": "L5"},
                    "configuration": {"operation": "echo"},
                    "capabilities": [DEVELOPMENT_CAPABILITY],
                },
            },
            source="l5-plugin.yaml",
        )


def test_admin_scan_and_promote_do_not_auto_bind(client: TestClient) -> None:
    connectors = client.get("/api/v1/admin/connectors")
    assert connectors.status_code == 200, connectors.text
    spi = next(
        item for item in connectors.json() if item["name"] == "obsion-connector-sdk-development"
    )
    assert spi["plugin"]["status"] == "passed"
    assert spi["plugin"]["lifecycle"] == "registered"
    connector_id = spi["id"]
    before = client.get("/api/v1/admin/capabilities")
    assert before.status_code == 200
    scan = client.post(f"/api/v1/admin/connectors/{connector_id}/scan")
    assert scan.status_code == 200, scan.text
    body = scan.json()["scan"]
    assert body["status"] == "passed"
    assert body["signature"] == "not_required"
    assert "credential" not in str(body).casefold()
    promote = client.post(f"/api/v1/admin/connectors/{connector_id}/promote")
    assert promote.status_code == 200, promote.text
    assert promote.json()["status"] == "ACTIVE"
    after = client.get("/api/v1/admin/capabilities")
    assert [item["name"] for item in before.json()] == [item["name"] for item in after.json()]

    created = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "phase47-l5-denied",
            "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
            "environment": "development",
            "status": "DRAFT",
            "declared_grants": ["connector.sdk.invoke"],
            "allowed_egress": [],
            "configuration": {"operation": "echo", "plugin": {**DEVELOPMENT_PLUGIN, "risk": "L5"}},
        },
    )
    assert created.status_code == 422, created.text
    assert created.json()["code"] == "v1_production_action_boundary"

    knowledge = next(item for item in connectors.json() if item["name"] == "obsion-knowledge-index")
    knowledge_scan = client.post(f"/api/v1/admin/connectors/{knowledge['id']}/scan")
    assert knowledge_scan.status_code == 200, knowledge_scan.text
    assert knowledge_scan.json()["scan"]["status"] == "not_applicable"
    denied = client.post(f"/api/v1/admin/connectors/{knowledge['id']}/promote")
    assert denied.status_code == 422
    assert denied.json()["code"] == "connector_handler_missing"


def test_plugin_governance_is_static_and_not_a_loader() -> None:
    source = (_SOURCE_ROOT / "capabilities" / "plugin_governance.py").read_text(encoding="utf-8")
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
        if imported in _FORBIDDEN_PLUGIN_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_PLUGIN_IMPORTS)
    ]
    assert violations == []
    assert "HMAC-SHA256" in source
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "plugin_governance" not in runtime
    assert "ConnectorSdkRuntime" not in runtime
