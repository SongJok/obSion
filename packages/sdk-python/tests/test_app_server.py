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
