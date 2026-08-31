import asyncio
import json
from typing import Any

import pytest

from obsion_sdk import AsyncObsionAppServerClient, ObsionAppServerError


class FakeTransport:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
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
        if request["method"] == "server.initialize":
            result: Any = {"protocol_version": "2026-08-26", "methods": []}
        elif request["method"] == "thread.create":
            result = {"id": "thread-1", "title": request["params"]["title"]}
        elif request["method"] == "workspace.list":
            result = [{"id": "workspace-1"}]
        elif request["method"] == "thread.archive":
            result = {"id": request["params"]["thread_id"], "status": "ARCHIVED"}
        elif request["method"] == "approval.decide":
            result = {
                "id": request["params"]["approval_id"],
                "status": "APPROVED" if request["params"]["decision"] == "approve" else "REJECTED",
            }
        elif request["method"] == "run.get":
            await self.incoming.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "run.completed",
                        "params": {"event": {"run_sequence": 8}},
                    }
                )
            )
            await self.incoming.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {
                            "code": -32004,
                            "message": "Run was not found",
                            "data": {
                                "code": "resource_not_found",
                                "status": 404,
                                "correlation_id": "correlation-1",
                                "details": {"resource": "Run"},
                            },
                        },
                    }
                )
            )
            return
        else:
            result = {}
        await self.incoming.put(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
        )

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_app_server_client_initializes_correlates_and_streams_notifications() -> None:
    transport = FakeTransport()

    async def factory(_url: str, protocols: list[str], headers: dict[str, str]):
        assert protocols == ["obsion.jsonrpc.v1"]
        assert headers == {"Authorization": "Bearer token"}
        return transport

    client = AsyncObsionAppServerClient(
        "wss://obsion.example/api/v1/app-server",
        token="token",
        transport_factory=factory,
    )
    initialized = await client.connect()
    assert initialized["protocol_version"] == "2026-08-26"
    thread = await client.create_thread(
        "workspace-1",
        "Investigation",
        client_request_id="thread-create-1",
    )
    assert thread == {"id": "thread-1", "title": "Investigation"}
    assert transport.sent[-1]["params"]["client_request_id"] == "thread-create-1"

    with pytest.raises(ObsionAppServerError) as captured:
        await client.request("run.get", {"run_id": "run-404"})
    assert captured.value.code == "resource_not_found"
    assert captured.value.status == 404
    assert captured.value.correlation_id == "correlation-1"

    stream = client.notifications()
    event = await anext(stream)
    assert event["method"] == "run.completed"
    assert event["params"]["event"]["run_sequence"] == 8
    await stream.aclose()
    await client.aclose()
    assert transport.closed is True


def test_app_server_url_accepts_api_root_and_origin() -> None:
    from obsion_sdk import app_server_url_from_api_url, new_client_request_id

    assert (
        app_server_url_from_api_url("https://obsion.example/api/v1")
        == "wss://obsion.example/api/v1/app-server"
    )
    assert (
        app_server_url_from_api_url("http://127.0.0.1:8080")
        == "ws://127.0.0.1:8080/api/v1/app-server"
    )
    key = new_client_request_id("cli")
    assert key.startswith("cli-")


@pytest.mark.asyncio
async def test_app_server_client_covers_thread_approval_and_artifact_methods() -> None:
    transport = FakeTransport()

    async def factory(_url: str, _protocols: list[str], _headers: dict[str, str]):
        return transport

    client = AsyncObsionAppServerClient(
        "wss://obsion.example/api/v1/app-server",
        token="token",
        transport_factory=factory,
    )
    await client.connect()
    assert await client.list_workspaces() == [{"id": "workspace-1"}]
    archived = await client.archive_thread("thread-1", client_request_id="archive-1")
    assert archived["status"] == "ARCHIVED"
    decided = await client.decide_approval(
        "approval-1",
        client_request_id="decide-1",
        approve=True,
        reason="Verified the evidence chain",
    )
    assert decided["status"] == "APPROVED"
    await client.aclose()
