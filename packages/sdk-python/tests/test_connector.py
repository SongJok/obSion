from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obsion_sdk.connector import (
    ConnectorExecuteContext,
    ConnectorExecuteRequest,
    ConnectorHealth,
    ConnectorInvocationContext,
    ConnectorSdkError,
    DevelopmentEchoConnector,
    assert_no_forbidden_fields,
    discovery_as_dict,
    health_as_dict,
    parse_execute_request,
)


@pytest.mark.asyncio
async def test_development_echo_connector_implements_health_discover_execute() -> None:
    adapter = DevelopmentEchoConnector()
    invocation = ConnectorInvocationContext(
        connector_name="obsion-connector-sdk-development",
        connector_type=DevelopmentEchoConnector.CONNECTOR_TYPE,
        environment="development",
    )
    health = await adapter.health(invocation)
    assert health.status == "ready"
    discovery = await adapter.discover(invocation)
    assert discovery.operations[0].capability == DevelopmentEchoConnector.CAPABILITY
    assert discovery.operations[0].side_effect is False
    result = await adapter.execute(
        ConnectorExecuteRequest(operation="echo", arguments={"ping": "pong"}),
        ConnectorExecuteContext(
            connector_name=invocation.connector_name,
            connector_type=invocation.connector_type,
            environment=invocation.environment,
            operation="echo",
            run_id="run-1",
            credential="connector-secret-token",
        ),
    )
    assert result["protocol"] == "connector-sdk"
    assert result["adapter"] == "in-process"
    assert result["echo"] == {"ping": "pong"}
    assert "connector-secret-token" not in str(result)


def test_execute_request_and_discovery_reject_secrets_and_endpoints() -> None:
    request = parse_execute_request({"operation": "echo", "arguments": {"q": "ok"}})
    assert request.operation == "echo"
    assert request.arguments == {"q": "ok"}
    with pytest.raises(ConnectorSdkError) as invalid:
        parse_execute_request({"operation": "", "arguments": {}})
    assert invalid.value.code == "capability_input_invalid"
    with pytest.raises(ConnectorSdkError) as secret:
        assert_no_forbidden_fields({"token": "abc"})
    assert secret.value.code == "capability_output_invalid"
    with pytest.raises(ConnectorSdkError) as endpoint:
        assert_no_forbidden_fields({"endpoint": "https://evil.example"})
    assert endpoint.value.code == "capability_output_invalid"
    with pytest.raises(ConnectorSdkError) as leaked:
        assert_no_forbidden_fields(
            {"echo": "connector-secret-token"},
            credential="connector-secret-token",
        )
    assert leaked.value.code == "capability_output_invalid"


@pytest.mark.asyncio
async def test_health_and_discovery_views_are_credential_free() -> None:
    adapter = DevelopmentEchoConnector()
    payload = health_as_dict(
        ConnectorHealth(status="ready", details={"protocol": "connector-sdk"}),
        checked_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert payload["adapter"] == "connector-sdk"
    assert "credential" not in str(payload).casefold()
    discovery = discovery_as_dict(
        await adapter.discover(
            ConnectorInvocationContext(
                connector_name="obsion-connector-sdk-development",
                connector_type=adapter.CONNECTOR_TYPE,
                environment="development",
            )
        )
    )
    assert discovery["operations"][0]["capability"] == adapter.CAPABILITY
    assert "endpoint" not in str(discovery).casefold()


def test_plugin_declaration_canonical_hmac_is_stable() -> None:
    from obsion_sdk.connector import (
        DEVELOPMENT_PLUGIN,
        parse_plugin_declaration,
        sign_plugin_declaration,
        verify_plugin_signature,
    )

    declaration = parse_plugin_declaration(DEVELOPMENT_PLUGIN)
    signature = sign_plugin_declaration(declaration, "obsion-plugin-test-key")
    assert verify_plugin_signature(declaration, signature, "obsion-plugin-test-key")
    assert not verify_plugin_signature(declaration, signature, "different-key")
    with pytest.raises(ConnectorSdkError) as denied:
        parse_plugin_declaration({**DEVELOPMENT_PLUGIN, "network": "unrestricted"})
    assert denied.value.code == "capability_input_invalid"
    with pytest.raises(ConnectorSdkError):
        parse_plugin_declaration({**DEVELOPMENT_PLUGIN, "secrets": ["inline-password"]})
    with pytest.raises(ConnectorSdkError):
        parse_plugin_declaration({**DEVELOPMENT_PLUGIN, "pip": "evil"})
