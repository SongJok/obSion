import json
from types import SimpleNamespace

import httpx
import pytest

from obsion_im.dingtalk import configure_stream_connection


def client():
    return SimpleNamespace(
        OPEN_CONNECTION_API="https://api.dingtalk.com/v1.0/gateway/connections/open",
        credential=SimpleNamespace(client_id="test-app", client_secret="PRIVATE_APP_SECRET"),
        callback_handler_map={"/v1.0/im/bot/messages/get": object()},
        _is_event_required=False,
        get_host_ip=lambda: "127.0.0.1",
    )


def test_timed_out_handshake_can_retry_without_logging_credentials(caplog):
    sdk = client()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"] == {"connect": 5, "read": 10, "write": 10, "pool": 5}
        body = json.loads(request.content)
        assert body["clientSecret"] == "PRIVATE_APP_SECRET"
        assert body["subscriptions"] == [{"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}]
        if len(calls) == 1:
            raise httpx.ReadTimeout("PRIVATE_APP_SECRET PRIVATE_TICKET", request=request)
        return httpx.Response(
            200, json={"endpoint": "wss://stream.dingtalk.com/connect", "ticket": "PRIVATE_TICKET"}
        )

    configure_stream_connection(sdk, transport=httpx.MockTransport(handler))
    with caplog.at_level("INFO", logger="obsion_im.stream"):
        assert sdk.open_connection() is None
        result = sdk.open_connection()
    assert result["ticket"] == "PRIVATE_TICKET"
    assert "reason=timeout" in caplog.text
    assert "connection_ticket_ready" in caplog.text
    assert "PRIVATE_" not in caplog.text
    assert len(calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"endpoint": "http://insecure.example", "ticket": "PRIVATE_TICKET"},
        {"endpoint": "wss://stream.dingtalk.com?ticket=PRIVATE_TICKET", "ticket": "PRIVATE_TICKET"},
    ],
)
def test_bad_handshake_is_not_a_ready_connection(payload, caplog):
    sdk = client()
    configure_stream_connection(
        sdk, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    assert sdk.open_connection() is None
    assert "PRIVATE_" not in caplog.text


def test_origin_change_never_receives_an_installed_credential(caplog):
    sdk = client()
    sdk.OPEN_CONNECTION_API = "https://different.example/connect"

    def handler(request):
        pytest.fail("Credential must not leave the installed provider origin")

    configure_stream_connection(sdk, transport=httpx.MockTransport(handler))
    assert sdk.open_connection() is None
    assert "reason=origin" in caplog.text
    assert "PRIVATE_" not in caplog.text


@pytest.mark.asyncio
async def test_websocket_lifecycle_is_observed_without_enabling_sdk_logs(caplog):
    from unittest.mock import AsyncMock

    sdk = client()
    original = AsyncMock()
    sdk.keepalive = original
    configure_stream_connection(sdk)
    private_socket = object()
    with caplog.at_level("INFO", logger="obsion_im.stream"):
        await sdk.keepalive(private_socket, ping_interval=60)
    original.assert_awaited_once_with(private_socket, ping_interval=60)
    assert "websocket_connected" in caplog.text
    assert "websocket_keepalive_stopped" in caplog.text
    assert "PRIVATE_" not in caplog.text
