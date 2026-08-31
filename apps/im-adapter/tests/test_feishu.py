from __future__ import annotations

import json
import os

import httpx
import pytest

from obsion_im.channel import OutboundMessage
from obsion_im.config import FEISHU_HTTP_DELIVERY, FeishuCredentials, ImError
from obsion_im.feishu import (
    MESSAGE_PATH,
    TENANT_TOKEN_PATH,
    FeishuClient,
    FeishuHttpChannel,
)


def _credentials() -> FeishuCredentials:
    return FeishuCredentials(app_id="cli_test_app", app_secret="test-app-secret")


@pytest.mark.asyncio
async def test_feishu_client_authenticates_caches_token_and_sends_idempotently() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == TENANT_TOKEN_PATH:
            payload = json.loads(request.content)
            assert payload == {"app_id": "cli_test_app", "app_secret": "test-app-secret"}
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": "tenant-token",
                    "expire": 7200,
                },
            )
        assert request.url.path == MESSAGE_PATH
        assert request.url.params["receive_id_type"] == "chat_id"
        assert request.headers["Authorization"] == "Bearer tenant-token"
        payload = json.loads(request.content)
        assert payload["receive_id"] == "oc_ops"
        assert payload["msg_type"] == "text"
        assert payload["uuid"] == "run-1"
        assert json.loads(payload["content"]) == {"text": "根因已记录。"}
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": {"message_id": "om_1"}})

    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        first = await client.send_text(
            chat_id="oc_ops",
            text="根因已记录。",
            idempotency_key="run-1",
        )
        second = await client.send_text(
            chat_id="oc_ops",
            text="根因已记录。",
            idempotency_key="run-1",
        )
    finally:
        await client.aclose()
    assert first.message_id == second.message_id == "om_1"
    assert [request.url.path for request in requests].count(TENANT_TOKEN_PATH) == 1
    assert [request.url.path for request in requests].count(MESSAGE_PATH) == 2


@pytest.mark.asyncio
async def test_feishu_client_retries_only_bounded_transient_failures() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"code": 1, "msg": "unavailable"})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "tenant_access_token": "tenant-token",
                "expire": 7200,
            },
        )

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    client = FeishuClient(
        _credentials(),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )
    try:
        health = await client.health()
    finally:
        await client.aclose()
    assert health["authenticated"] is True
    assert attempts == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_feishu_errors_redact_credentials_and_tokens() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": "tenant-token",
                    "expire": 7200,
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 230001,
                "msg": "test-app-secret tenant-token cli_test_app rejected",
            },
        )

    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ImError) as raised:
            await client.send_text(chat_id="oc_ops", text="hello", idempotency_key="run-1")
    finally:
        await client.aclose()
    message = str(raised.value)
    assert "test-app-secret" not in message
    assert "tenant-token" not in message
    assert "cli_test_app" not in message
    assert message.count("[redacted]") == 3


@pytest.mark.asyncio
async def test_feishu_channel_rejects_cross_vendor_delivery() -> None:
    client = FeishuClient(
        _credentials(),
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    channel = FeishuHttpChannel(client)
    message = OutboundMessage(
        conversation_id="cid",
        text="reply",
        run_id="run-1",
        thread_id="thread-1",
        channel="dingtalk",
        delivery=FEISHU_HTTP_DELIVERY,
    )
    try:
        with pytest.raises(ImError, match="another vendor"):
            await channel.reply(message)
    finally:
        await channel.aclose()


def test_feishu_credentials_are_not_represented() -> None:
    credentials = _credentials()
    rendered = repr(credentials)
    assert credentials.app_id not in rendered
    assert credentials.app_secret not in rendered


@pytest.mark.asyncio
@pytest.mark.live
async def test_feishu_live_tenant_token_when_operator_enables_it() -> None:
    if os.environ.get("OBSION_FEISHU_LIVE") != "1":
        pytest.skip("Live Feishu HTTP is operator-owned")
    app_id = (os.environ.get("OBSION_FEISHU_APP_ID") or "").strip()
    app_secret = (os.environ.get("OBSION_FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        pytest.skip("Live Feishu credentials are not configured")
    client = FeishuClient(FeishuCredentials(app_id=app_id, app_secret=app_secret))
    try:
        health = await client.health()
    finally:
        await client.aclose()
    assert health["authenticated"] is True
    assert health["delivery"] == FEISHU_HTTP_DELIVERY
    assert health["channel"] == "feishu"
    assert int(health["expires_in_seconds"]) > 0
