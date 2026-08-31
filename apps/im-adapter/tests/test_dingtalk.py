from __future__ import annotations

import json

import httpx
import pytest

from obsion_im.channel import OutboundMessage
from obsion_im.config import DINGTALK_HTTP_DELIVERY, DingTalkCredentials, ImError
from obsion_im.dingtalk import (
    MESSAGE_PATH,
    TOKEN_PATH,
    DingTalkClient,
    DingTalkHttpChannel,
)


def _credentials() -> DingTalkCredentials:
    return DingTalkCredentials(app_key="ding-test-key", app_secret="ding-test-secret")


@pytest.mark.asyncio
async def test_dingtalk_client_authenticates_caches_token_and_sends() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == TOKEN_PATH:
            assert request.url.params["appkey"] == "ding-test-key"
            assert request.url.params["appsecret"] == "ding-test-secret"
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "access_token": "ding-access-token",
                    "expires_in": 7200,
                },
            )
        assert request.url.path == MESSAGE_PATH
        assert request.url.params["access_token"] == "ding-access-token"
        payload = json.loads(request.content)
        assert payload["chatid"] == "cid-ops"
        assert payload["msg"] == {"msgtype": "text", "text": {"content": "根因已记录。"}}
        return httpx.Response(
            200,
            json={"errcode": 0, "errmsg": "ok", "messageId": "ding-msg-1"},
        )

    client = DingTalkClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        first = await client.send_text(
            chat_id="cid-ops",
            text="根因已记录。",
            idempotency_key="delivery-1",
        )
        second = await client.send_text(
            chat_id="cid-ops",
            text="根因已记录。",
            idempotency_key="delivery-1",
        )
    finally:
        await client.aclose()
    assert first.message_id == second.message_id == "ding-msg-1"
    assert [request.url.path for request in requests].count(TOKEN_PATH) == 1
    assert [request.url.path for request in requests].count(MESSAGE_PATH) == 2


@pytest.mark.asyncio
async def test_dingtalk_client_retries_transient_failures_and_redacts_secrets() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"errcode": 1, "errmsg": "unavailable"})
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "access_token": "ding-access-token",
                "expires_in": 7200,
            },
        )

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    client = DingTalkClient(
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
async def test_dingtalk_http_channel_rejects_other_vendors() -> None:
    client = DingTalkClient(
        _credentials(), transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    channel = DingTalkHttpChannel(client)
    try:
        with pytest.raises(ImError, match="another vendor"):
            await channel.reply(
                OutboundMessage(
                    conversation_id="cid-ops",
                    text="hi",
                    run_id="run-1",
                    thread_id="thread-1",
                    channel="feishu",
                    delivery=DINGTALK_HTTP_DELIVERY,
                )
            )
        with pytest.raises(ImError, match="dingtalk-http"):
            await channel.reply(
                OutboundMessage(
                    conversation_id="cid-ops",
                    text="hi",
                    run_id="run-1",
                    thread_id="thread-1",
                    channel="dingtalk",
                    delivery="local_outbox",
                )
            )
    finally:
        await channel.aclose()


@pytest.mark.asyncio
async def test_dingtalk_vendor_errors_redact_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errcode": 88,
                "errmsg": "invalid ding-test-secret for ding-test-key",
            },
        )

    client = DingTalkClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ImError, match=r"\[redacted\]") as exc:
            await client.health()
    finally:
        await client.aclose()
    assert "ding-test-secret" not in str(exc.value)
    assert "ding-test-key" not in str(exc.value)
