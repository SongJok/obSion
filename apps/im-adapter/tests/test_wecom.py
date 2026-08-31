from __future__ import annotations

import json

import httpx
import pytest

from obsion_im.channel import OutboundMessage
from obsion_im.config import WECOM_HTTP_DELIVERY, ImError, WeComCredentials
from obsion_im.wecom import (
    APPCHAT_MESSAGE_PATH,
    TOKEN_PATH,
    USER_MESSAGE_PATH,
    WeComClient,
    WeComHttpChannel,
)


def _credentials() -> WeComCredentials:
    return WeComCredentials(corp_id="ww-test-corp", corp_secret="ww-test-secret", agent_id=1000002)


@pytest.mark.asyncio
async def test_wecom_client_sends_appchat_when_conversation_differs_from_sender() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == TOKEN_PATH:
            assert request.url.params["corpid"] == "ww-test-corp"
            assert request.url.params["corpsecret"] == "ww-test-secret"
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "access_token": "ww-access-token",
                    "expires_in": 7200,
                },
            )
        assert request.url.path == APPCHAT_MESSAGE_PATH
        assert request.url.params["access_token"] == "ww-access-token"
        payload = json.loads(request.content)
        assert payload == {
            "chatid": "wr_ops",
            "msgtype": "text",
            "text": {"content": "根因已记录。"},
        }
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "ww-msg-1"})

    client = WeComClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        receipt = await client.send_text(
            conversation_id="wr_ops",
            text="根因已记录。",
            reply_to_sender_id="wecom-alice",
            idempotency_key="delivery-1",
        )
    finally:
        await client.aclose()
    assert receipt.message_id == "ww-msg-1"
    assert [request.url.path for request in requests] == [TOKEN_PATH, APPCHAT_MESSAGE_PATH]


@pytest.mark.asyncio
async def test_wecom_client_sends_user_message_for_direct_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "access_token": "ww-access-token",
                    "expires_in": 7200,
                },
            )
        assert request.url.path == USER_MESSAGE_PATH
        payload = json.loads(request.content)
        assert payload == {
            "touser": "wecom-alice",
            "msgtype": "text",
            "agentid": 1000002,
            "text": {"content": "直接回复"},
        }
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "ww-msg-2"})

    client = WeComClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        receipt = await client.send_text(
            conversation_id="wecom-alice",
            text="直接回复",
            reply_to_sender_id="wecom-alice",
            idempotency_key="delivery-2",
        )
    finally:
        await client.aclose()
    assert receipt.message_id == "ww-msg-2"


@pytest.mark.asyncio
async def test_wecom_http_channel_rejects_mismatched_delivery() -> None:
    client = WeComClient(
        _credentials(), transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    channel = WeComHttpChannel(client)
    try:
        with pytest.raises(ImError, match="wecom-http"):
            await channel.reply(
                OutboundMessage(
                    conversation_id="wecom-alice",
                    text="hi",
                    run_id="run-1",
                    thread_id="thread-1",
                    channel="wecom",
                    delivery="local_outbox",
                )
            )
        with pytest.raises(ImError, match="another vendor"):
            await channel.reply(
                OutboundMessage(
                    conversation_id="wecom-alice",
                    text="hi",
                    run_id="run-1",
                    thread_id="thread-1",
                    channel="dingtalk",
                    delivery=WECOM_HTTP_DELIVERY,
                )
            )
    finally:
        await channel.aclose()


@pytest.mark.asyncio
async def test_wecom_vendor_errors_redact_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errcode": 40001, "errmsg": "invalid ww-test-secret for ww-test-corp"},
        )

    client = WeComClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ImError, match=r"\[redacted\]") as exc:
            await client.health()
    finally:
        await client.aclose()
    assert "ww-test-secret" not in str(exc.value)
    assert "ww-test-corp" not in str(exc.value)
