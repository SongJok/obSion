from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import httpx
import pytest

from obsion_cli.config import CliSettings
from obsion_cli.runtime import ExperienceRuntime
from obsion_im.bridge import ImBridge
from obsion_im.channel import (
    DevelopmentImChannel,
    ImDeliveryReceipt,
    InboundMessage,
    OutboundMessage,
    conversation_thread_title,
)
from obsion_im.config import ImError
from obsion_sdk import AsyncObsionAppServerClient, AsyncObsionClient


class FakeAppServerTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.incoming.put_nowait(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "server.ready",
                    "params": {"protocol_version": "2026-08-26"},
                }
            )
        )

    async def send(self, message: str) -> None:
        request = json.loads(message)
        self.sent.append(request)
        method = request["method"]
        if method == "server.initialize":
            result: Any = {"protocol_version": "2026-08-26", "methods": []}
        elif method == "run.get":
            result = {"id": "run-1", "status": "COMPLETED"}
        else:
            result = {}
        await self.incoming.put(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
        )

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        return None


def _rest_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v1/experience/im/messages":
        body = json.loads(request.content)
        assert body["sender_id"] == "alice-stable"
        assert "display_name" not in body
        return httpx.Response(
            202,
            json={
                "binding_id": "binding-1",
                "channel": "development",
                "principal_id": "user-alice",
                "run_id": "run-1",
                "sender_id": body["sender_id"],
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "workspace_id": "workspace-1",
            },
        )
    if path == "/api/v1/runs/run-1":
        return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})
    if path == "/api/v1/runs/run-1/events":
        return httpx.Response(
            200,
            json=[
                {
                    "id": "event-1",
                    "name": "answer.delta",
                    "run_sequence": 1,
                    "payload": {"delta": "你好。"},
                }
            ],
        )
    if path.endswith("/steps"):
        return httpx.Response(200, json=[{"kind": "REFLECT"}, {"kind": "RESPOND"}])
    if path.endswith("/evidence") or path.endswith("/claims") or path.endswith("/artifacts"):
        return httpx.Response(200, json=[])
    return httpx.Response(404, json={"code": "resource_not_found", "message": path})


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_bridge_ingests_through_control_plane_identity_mapping() -> None:
    transport = FakeAppServerTransport()
    rest_paths: list[str] = []

    async def factory(
        _url: str, _protocols: list[str], _headers: dict[str, str]
    ) -> FakeAppServerTransport:
        return transport

    def handler(request: httpx.Request) -> httpx.Response:
        rest_paths.append(request.url.path)
        return _rest_handler(request)

    rest = AsyncObsionClient(
        "http://obsion.example",
        token="im-token",
        transport=httpx.MockTransport(handler),
    )
    app_server = AsyncObsionAppServerClient(
        "ws://obsion.example/api/v1/app-server",
        token="im-token",
        transport_factory=factory,
    )
    await app_server.connect()
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://obsion.example",
            token="im-token",
            protocol="app-server",
        ),
        rest=rest,
        app_server=app_server,
        request_id_factory=lambda prefix: f"{prefix}-fixed",
        sleep=_no_sleep,
    )
    channel = DevelopmentImChannel()
    bridge = ImBridge(runtime, channel)
    outbound = await bridge.handle(
        InboundMessage(
            conversation_id="ops",
            text="你好",
            sender_id="alice-stable",
            sender_display="Alice 昵称",
        )
    )
    await runtime.aclose()

    methods = [item["method"] for item in transport.sent]
    assert "turn.create" not in methods
    assert "thread.create" not in methods
    assert "/api/v1/experience/im/messages" in rest_paths
    assert outbound.text == "你好。"
    assert outbound.thread_id == "thread-1"
    assert outbound.run_id == "run-1"
    assert outbound.channel == "development"
    assert outbound.reply_to_sender_id == "alice-stable"
    assert channel.outbox == [outbound]
    assert channel.envelopes[0]["delivery"] == "local_outbox"
    assert channel.envelopes[0]["vendor"]["text"] == "你好。"
    assert "im-token" not in outbound.text
    assert conversation_thread_title("development", "ops") == "im:development:ops"


@pytest.mark.asyncio
async def test_bridge_rejects_empty_text() -> None:
    rest = AsyncObsionClient("http://obsion.example", token="im-token")
    runtime = ExperienceRuntime(
        CliSettings(base_url="http://obsion.example", token="im-token", protocol="rest"),
        rest=rest,
        sleep=_no_sleep,
    )
    bridge = ImBridge(runtime, DevelopmentImChannel())
    with pytest.raises(ImError, match="requires text"):
        await bridge.handle(
            InboundMessage(conversation_id="ops", text="   ", sender_id="alice-stable")
        )
    await runtime.aclose()


@pytest.mark.asyncio
async def test_bridge_rejects_missing_sender_id() -> None:
    rest = AsyncObsionClient("http://obsion.example", token="im-token")
    runtime = ExperienceRuntime(
        CliSettings(base_url="http://obsion.example", token="im-token", protocol="rest"),
        rest=rest,
        sleep=_no_sleep,
    )
    bridge = ImBridge(runtime, DevelopmentImChannel())
    with pytest.raises(ImError, match="stable sender id"):
        await bridge.handle(InboundMessage(conversation_id="ops", text="你好", sender_id="  "))
    await runtime.aclose()


@pytest.mark.asyncio
async def test_bridge_renders_feishu_outbound_into_the_local_outbox() -> None:
    transport = FakeAppServerTransport()

    async def factory(
        _url: str, _protocols: list[str], _headers: dict[str, str]
    ) -> FakeAppServerTransport:
        return transport

    rest = AsyncObsionClient(
        "http://obsion.example",
        token="im-token",
        transport=httpx.MockTransport(_rest_handler),
    )
    app_server = AsyncObsionAppServerClient(
        "ws://obsion.example/api/v1/app-server",
        token="im-token",
        transport_factory=factory,
    )
    await app_server.connect()
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://obsion.example",
            token="im-token",
            protocol="app-server",
        ),
        rest=rest,
        app_server=app_server,
        request_id_factory=lambda prefix: f"{prefix}-fixed",
        sleep=_no_sleep,
    )
    channel = DevelopmentImChannel()
    outbound = await ImBridge(runtime, channel).handle(
        InboundMessage(
            conversation_id="oc_ops",
            text="你好",
            sender_id="alice-stable",
            channel="feishu",
        )
    )
    await runtime.aclose()
    assert outbound.channel == "feishu"
    assert channel.envelopes[0]["delivery"] == "local_outbox"
    assert channel.envelopes[0]["vendor"]["receive_id"] == "oc_ops"
    assert "turn.create" not in [item["method"] for item in transport.sent]


@pytest.mark.asyncio
async def test_bridge_authorizes_and_records_live_feishu_delivery() -> None:
    app_server_transport = FakeAppServerTransport()
    rest_requests: list[tuple[str, str, dict[str, object]]] = []

    async def factory(
        _url: str, _protocols: list[str], _headers: dict[str, str]
    ) -> FakeAppServerTransport:
        return app_server_transport

    def rest_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        rest_requests.append((request.method, path, body))
        if path == "/api/v1/experience/im/messages":
            return httpx.Response(202, json={"run_id": "run-1", "thread_id": "thread-1"})
        if path == "/api/v1/runs/run-1":
            return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})
        if path == "/api/v1/runs/run-1/events":
            return httpx.Response(
                200,
                json=[{"name": "answer.delta", "payload": {"delta": "你好。"}}],
            )
        if path == "/api/v1/runs/run-1/artifacts":
            return httpx.Response(200, json=[])
        if path == "/api/v1/experience/im/runs/run-1/deliveries":
            return httpx.Response(
                200,
                json={
                    "id": "delivery-1",
                    "run_id": "run-1",
                    "channel": "feishu",
                    "conversation_id": "oc_ops",
                    "text": "你好。",
                    "content_fingerprint": hashlib.sha256("你好。".encode()).hexdigest(),
                    "idempotency_key": "delivery-1",
                    "status": "PENDING",
                    "attempt_count": 1,
                },
            )
        if path == "/api/v1/experience/im/deliveries/delivery-1/complete":
            return httpx.Response(200, json={"id": "delivery-1", "status": "SENT"})
        return httpx.Response(404, json={"code": "resource_not_found", "message": path})

    class LiveChannel:
        name = "feishu"
        delivery = "feishu_http"

        def __init__(self) -> None:
            self.outbound: OutboundMessage | None = None

        async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt:
            self.outbound = message
            return ImDeliveryReceipt(vendor_message_id="om_1")

        async def health(self) -> dict[str, object]:
            return {"authenticated": True}

        async def aclose(self) -> None:
            return None

    rest = AsyncObsionClient(
        "http://obsion.example",
        token="im-token",
        transport=httpx.MockTransport(rest_handler),
    )
    app_server = AsyncObsionAppServerClient(
        "ws://obsion.example/api/v1/app-server",
        token="im-token",
        transport_factory=factory,
    )
    await app_server.connect()
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://obsion.example",
            token="im-token",
            protocol="app-server",
        ),
        rest=rest,
        app_server=app_server,
        request_id_factory=lambda prefix: f"{prefix}-fixed",
        sleep=_no_sleep,
    )
    channel = LiveChannel()
    outbound = await ImBridge(runtime, channel).handle(
        InboundMessage(
            conversation_id="oc_ops",
            text="你好",
            sender_id="ou_alice",
            channel="feishu",
        )
    )
    await runtime.aclose()

    assert outbound.delivery_id == "delivery-1"
    assert channel.outbound == outbound
    assert (
        "POST",
        "/api/v1/experience/im/deliveries/delivery-1/complete",
        {"vendor_message_id": "om_1"},
    ) in rest_requests


@pytest.mark.asyncio
async def test_bridge_records_failed_feishu_delivery() -> None:
    app_server_transport = FakeAppServerTransport()

    async def factory(
        _url: str, _protocols: list[str], _headers: dict[str, str]
    ) -> FakeAppServerTransport:
        return app_server_transport

    rest_requests: list[tuple[str, str, dict[str, object]]] = []

    def rest_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        rest_requests.append((request.method, path, body))
        if path == "/api/v1/experience/im/messages":
            return httpx.Response(202, json={"run_id": "run-1", "thread_id": "thread-1"})
        if path == "/api/v1/runs/run-1":
            return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})
        if path == "/api/v1/runs/run-1/events":
            return httpx.Response(
                200,
                json=[{"name": "answer.delta", "payload": {"delta": "你好。"}}],
            )
        if path == "/api/v1/runs/run-1/artifacts":
            return httpx.Response(200, json=[])
        if path == "/api/v1/experience/im/runs/run-1/deliveries":
            return httpx.Response(
                200,
                json={
                    "id": "delivery-1",
                    "run_id": "run-1",
                    "channel": "feishu",
                    "conversation_id": "oc_ops",
                    "text": "你好。",
                    "content_fingerprint": hashlib.sha256("你好。".encode()).hexdigest(),
                    "idempotency_key": "delivery-1",
                    "status": "PENDING",
                    "attempt_count": 1,
                },
            )
        if path == "/api/v1/experience/im/deliveries/delivery-1/fail":
            return httpx.Response(200, json={"id": "delivery-1", "status": "FAILED"})
        return httpx.Response(404, json={"code": "resource_not_found", "message": path})

    class FailingChannel:
        name = "feishu"
        delivery = "feishu_http"

        async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt:
            raise ImError("Feishu HTTP request failed with status 503")

        async def health(self) -> dict[str, object]:
            return {"authenticated": False}

        async def aclose(self) -> None:
            return None

    rest = AsyncObsionClient(
        "http://obsion.example",
        token="im-token",
        transport=httpx.MockTransport(rest_handler),
    )
    app_server = AsyncObsionAppServerClient(
        "ws://obsion.example/api/v1/app-server",
        token="im-token",
        transport_factory=factory,
    )
    await app_server.connect()
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://obsion.example",
            token="im-token",
            protocol="app-server",
        ),
        rest=rest,
        app_server=app_server,
        request_id_factory=lambda prefix: f"{prefix}-fixed",
        sleep=_no_sleep,
    )
    with pytest.raises(ImError, match="status 503"):
        await ImBridge(runtime, FailingChannel()).handle(
            InboundMessage(
                conversation_id="oc_ops",
                text="你好",
                sender_id="ou_alice",
                channel="feishu",
            )
        )
    await runtime.aclose()
    assert (
        "POST",
        "/api/v1/experience/im/deliveries/delivery-1/fail",
        {"failure_code": "vendor_request_failed"},
    ) in rest_requests
