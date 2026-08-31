from __future__ import annotations

import asyncio
import json

import httpx
from conftest import TEST_BEARER_TOKEN
from fastapi.testclient import TestClient

from obsion_cli.config import CliSettings
from obsion_cli.runtime import AskResult, ExperienceRuntime
from obsion_sdk import AsyncObsionClient

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _rest_client(test_client: TestClient) -> AsyncObsionClient:
    def handler(request: httpx.Request) -> httpx.Response:
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length"}
        }
        response = test_client.request(
            request.method,
            target,
            headers=headers,
            content=request.content,
        )
        return httpx.Response(
            response.status_code,
            headers=dict(response.headers),
            content=response.content,
        )

    return AsyncObsionClient(
        "http://testserver",
        token=TEST_BEARER_TOKEN,
        transport=httpx.MockTransport(handler),
    )


async def _ask(test_client: TestClient) -> AskResult:
    runtime = ExperienceRuntime(
        CliSettings(
            base_url="http://testserver",
            token=TEST_BEARER_TOKEN,
            protocol="rest",
            poll_interval_seconds=0.05,
            wait_timeout_seconds=15,
        ),
        rest=_rest_client(test_client),
    )
    try:
        return await runtime.ask("你好", workspace_name="CLI")
    finally:
        await runtime.aclose()


def test_phase26_cli_ask_completes_a_governed_greeting_run(client: TestClient) -> None:
    result = asyncio.run(_ask(client))

    assert result.run["status"] == "COMPLETED"
    assert result.run["status"] in TERMINAL
    assert result.thread["id"]
    assert result.turn["id"]
    names = {event["name"] for event in result.events}
    assert {"intent.detected", "plan.created", "run.completed"}.issubset(names)
    kinds = {step["kind"] for step in result.steps}
    assert {"OBSERVE", "UNDERSTAND", "PLAN", "VERIFY", "REFLECT", "RESPOND"} <= kinds
    dumped = json.dumps(result.as_dict())
    assert TEST_BEARER_TOKEN not in dumped
    assert "password" not in dumped.lower()
