from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from obsion_cli.config import CliSettings
from obsion_cli.render import render_ask
from obsion_cli.runtime import ExperienceRuntime
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
        elif method == "workspace.list":
            result = [{"id": "workspace-1", "name": "CLI"}]
        elif method == "thread.create":
            result = {
                "id": "thread-1",
                "workspace_id": request["params"]["workspace_id"],
                "title": request["params"]["title"],
                "status": "ACTIVE",
            }
        elif method == "turn.create":
            result = {
                "turn": {"id": "turn-1", "thread_id": request["params"]["thread_id"]},
                "run": {"id": "run-1", "status": "RUNNING"},
            }
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
    if path == "/api/v1/workspaces" and request.method == "GET":
        return httpx.Response(200, json=[{"id": "workspace-1", "name": "CLI"}])
    if path == "/api/v1/workspaces":
        return httpx.Response(201, json={"id": "workspace-1", "name": "CLI"})
    if path.endswith("/turns"):
        return httpx.Response(
            202,
            json={
                "turn": {"id": "turn-1"},
                "run": {"id": "run-1", "status": "RUNNING"},
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
                    "name": "intent.detected",
                    "run_sequence": 1,
                    "payload": {},
                },
                {
                    "id": "event-2",
                    "name": "answer.delta",
                    "run_sequence": 2,
                    "payload": {"delta": "你好。", "final": True},
                },
                {
                    "id": "event-3",
                    "name": "run.completed",
                    "run_sequence": 3,
                    "payload": {},
                },
            ],
        )
    if path.endswith("/steps"):
        return httpx.Response(200, json=[{"kind": "OBSERVE"}, {"kind": "RESPOND"}])
    if path.endswith("/evidence"):
        return httpx.Response(200, json=[])
    if path.endswith("/claims"):
        return httpx.Response(200, json=[])
    if path.endswith("/artifacts"):
        return httpx.Response(200, json=[])
    if path.endswith("/threads") and request.method == "POST":
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={"id": "thread-1", "title": body["title"], "status": "ACTIVE"},
        )
    return httpx.Response(404, json={"code": "resource_not_found", "message": path})


@pytest.mark.asyncio
async def test_ask_uses_app_server_for_thread_and_turn_mutations() -> None:
    transport = FakeAppServerTransport()

    async def factory(
        _url: str, _protocols: list[str], _headers: dict[str, str]
    ) -> FakeAppServerTransport:
        return transport

    rest = AsyncObsionClient(
        "http://obsion.example",
        token="token",
        transport=httpx.MockTransport(_rest_handler),
    )
    app_server = AsyncObsionAppServerClient(
        "ws://obsion.example/api/v1/app-server",
        token="token",
        transport_factory=factory,
    )
    await app_server.connect()
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://obsion.example",
            token="token",
            protocol="app-server",
        ),
        rest=rest,
        app_server=app_server,
        request_id_factory=lambda prefix: f"{prefix}-fixed",
        sleep=_no_sleep,
    )
    result = await runtime.ask("你好")
    await runtime.aclose()

    methods = [item["method"] for item in transport.sent]
    assert "thread.create" in methods
    assert "turn.create" in methods
    assert result.answer == "你好。"
    assert result.run["status"] == "COMPLETED"
    assert "正在理解问题" in render_ask(result, json_output=False)
    assert "secret" not in render_ask(result, json_output=True)


@pytest.mark.asyncio
async def test_ask_rest_protocol_creates_workspace_thread_and_run() -> None:
    rest = AsyncObsionClient(
        "http://obsion.example",
        token="token",
        transport=httpx.MockTransport(_rest_handler),
    )
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://obsion.example",
            token="token",
            protocol="rest",
        ),
        rest=rest,
        sleep=_no_sleep,
    )
    result = await runtime.ask("你好")
    await runtime.aclose()
    assert result.thread["id"] == "thread-1"
    assert result.answer == "你好。"


async def _no_sleep(_seconds: float) -> None:
    return None
