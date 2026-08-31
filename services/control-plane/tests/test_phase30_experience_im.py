from __future__ import annotations

import asyncio

import httpx
from conftest import TEST_BEARER_TOKEN
from fastapi.testclient import TestClient

from obsion_cli.config import CliSettings
from obsion_cli.runtime import ExperienceRuntime
from obsion_im.bridge import ImBridge
from obsion_im.channel import DevelopmentImChannel, InboundMessage
from obsion_sdk import AsyncObsionClient


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


def _bind_current_principal(client: TestClient, sender_id: str) -> str:
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    principal_id = session.json()["principal_id"]
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "development",
            "sender_id": sender_id,
            "user_id": principal_id,
        },
    )
    assert binding.status_code == 201, binding.text
    return principal_id


async def _conversation(test_client: TestClient) -> tuple[str, str, str, list[dict[str, object]]]:
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
    channel = DevelopmentImChannel()
    try:
        first = await ImBridge(runtime, channel).handle(
            InboundMessage(
                conversation_id="ops-room",
                text="你好",
                sender_id="phase30-ops",
            )
        )
        second = await ImBridge(runtime, channel).handle(
            InboundMessage(
                conversation_id="ops-room",
                text="继续",
                sender_id="phase30-ops",
            )
        )
        steps = await runtime.list_run_steps(first.run_id)
        return first.thread_id, second.thread_id, first.text, steps
    finally:
        await runtime.aclose()


def test_phase30_im_ingest_reuses_one_thread_for_a_conversation(client: TestClient) -> None:
    _bind_current_principal(client, "phase30-ops")
    first_thread, second_thread, answer, steps = asyncio.run(_conversation(client))
    assert first_thread
    assert first_thread == second_thread
    assert TEST_BEARER_TOKEN not in answer
    assert "password" not in answer.lower()
    kinds = {str(step.get("kind")) for step in steps}
    assert {"OBSERVE", "UNDERSTAND", "PLAN", "VERIFY", "REFLECT", "RESPOND"} <= kinds
    turns = client.get(f"/api/v1/threads/{first_thread}/turns")
    assert turns.status_code == 200, turns.text
    created_by = {item["created_by"] for item in turns.json()}
    session = client.get("/api/v1/auth/session")
    assert created_by == {session.json()["principal_id"]}
