from __future__ import annotations

import json
import os

import httpx
import pytest

from obsion_im.channel import OutboundMessage
from obsion_im.config import FEISHU_HTTP_DELIVERY, FeishuCredentials, ImError
from obsion_im.feishu import (
    CHATS_PATH,
    MESSAGE_PATH,
    TENANT_TOKEN_PATH,
    FeishuClient,
    FeishuDeniedError,
    FeishuHttpChannel,
)


def _emit_probe_result(probe: str, classification: str, detail: str) -> None:
    """Record a live-probe outcome when the evidence recorder requests it."""

    directory = os.environ.get("OBSION_LIVE_PROBE_DIR", "").strip()
    if not directory:
        return
    payload = {"probe": probe, "classification": classification, "detail": detail[:240]}
    from pathlib import Path

    Path(directory, f"{probe}.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
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
    _emit_probe_result("feishu-tenant-token", "passed", "tenant token authenticated")


def _token_then_chats_handler(
    chats_payload: dict[str, object],
) -> tuple[list[httpx.Request], object]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == TENANT_TOKEN_PATH:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "tenant_access_token": "tenant-token",
                    "expire": 7200,
                },
            )
        assert request.url.path == CHATS_PATH
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer tenant-token"
        assert not request.content
        return httpx.Response(200, json=chats_payload)

    return requests, handler


@pytest.mark.asyncio
async def test_feishu_client_lists_chats_with_bounded_page() -> None:
    requests, handler = _token_then_chats_handler(
        {
            "code": 0,
            "msg": "ok",
            "data": {
                "items": [
                    {"chat_id": "oc_ops", "name": "运维群"},
                    {"chat_id": "oc_empty"},
                ],
                "has_more": True,
            },
        }
    )
    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        chats = await client.list_chats(page_size=20)
    finally:
        await client.aclose()
    assert [chat.chat_id for chat in chats] == ["oc_ops", "oc_empty"]
    assert chats[0].name == "运维群"
    assert chats[1].name == ""
    list_request = requests[-1]
    assert list_request.url.params["page_size"] == "20"


@pytest.mark.asyncio
async def test_feishu_client_list_chats_rejects_out_of_range_page_size() -> None:
    client = FeishuClient(
        _credentials(),
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    try:
        with pytest.raises(ImError, match="page size"):
            await client.list_chats(page_size=0)
        with pytest.raises(ImError, match="page size"):
            await client.list_chats(page_size=101)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_feishu_client_list_chats_handles_missing_items() -> None:
    _, handler = _token_then_chats_handler({"code": 0, "msg": "ok", "data": {}})
    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        assert await client.list_chats() == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_feishu_client_list_chats_fails_closed_on_malformed_items() -> None:
    _, handler = _token_then_chats_handler(
        {"code": 0, "msg": "ok", "data": {"items": [{"name": "no-id"}]}}
    )
    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ImError, match="chat_id"):
            await client.list_chats()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_feishu_client_list_chats_redacts_vendor_errors() -> None:
    _, handler = _token_then_chats_handler(
        {"code": 230001, "msg": "app cli_test_app test-app-secret tenant-token denied"}
    )
    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ImError) as captured:
            await client.list_chats()
    finally:
        await client.aclose()
    message = str(captured.value)
    assert "cli_test_app" not in message
    assert "test-app-secret" not in message
    assert "tenant-token" not in message
    assert message.count("[redacted]") == 3


@pytest.mark.asyncio
@pytest.mark.live
async def test_feishu_live_chat_listing_when_operator_enables_it() -> None:
    if os.environ.get("OBSION_FEISHU_LIVE") != "1":
        pytest.skip("Live Feishu HTTP is operator-owned")
    app_id = (os.environ.get("OBSION_FEISHU_APP_ID") or "").strip()
    app_secret = (os.environ.get("OBSION_FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        pytest.skip("Live Feishu credentials are not configured")
    client = FeishuClient(FeishuCredentials(app_id=app_id, app_secret=app_secret))
    try:
        try:
            chats = await client.list_chats()
        except FeishuDeniedError:
            # The tenant has not granted an im:chat scope; fail-closed denial is a
            # valid classified outcome for this non-sending probe.
            _emit_probe_result("feishu-chat-listing", "denied", "FeishuDeniedError")
            return
    finally:
        await client.aclose()
    assert all(chat.chat_id for chat in chats)
    _emit_probe_result("feishu-chat-listing", "passed", f"chats={len(chats)}")


@pytest.mark.asyncio
@pytest.mark.feishu_send_live
async def test_feishu_send_live_reply_when_operator_enables_it() -> None:
    if os.environ.get("OBSION_FEISHU_SEND_LIVE") != "1":
        pytest.skip("Live Feishu send is operator-owned")
    app_id = (os.environ.get("OBSION_FEISHU_APP_ID") or "").strip()
    app_secret = (os.environ.get("OBSION_FEISHU_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("OBSION_FEISHU_LIVE_CHAT_ID") or "").strip()
    if not app_id or not app_secret:
        pytest.skip("Live Feishu credentials are not configured")
    if not chat_id:
        pytest.skip("Live Feishu send requires an explicit OBSION_FEISHU_LIVE_CHAT_ID")
    client = FeishuClient(FeishuCredentials(app_id=app_id, app_secret=app_secret))
    channel = FeishuHttpChannel(client)
    message = OutboundMessage(
        conversation_id=chat_id,
        text="[Obsion live validation] feishu-http operator probe; no action required.",
        run_id="live-validation",
        thread_id="live-validation",
        channel="feishu",
        delivery=FEISHU_HTTP_DELIVERY,
    )
    try:
        receipt = await channel.reply(message)
    finally:
        await channel.aclose()
    assert receipt.vendor_message_id
    _emit_probe_result("feishu-send-probe", "passed", f"message_id={receipt.vendor_message_id}")


@pytest.mark.asyncio
async def test_feishu_client_classifies_http400_scope_denial_from_envelope() -> None:
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
            400,
            json={
                "code": 99991672,
                "msg": "Access denied. scopes required cli_test_app test-app-secret",
            },
        )

    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(FeishuDeniedError) as captured:
            await client.list_chats()
    finally:
        await client.aclose()
    message = str(captured.value)
    assert "99991672" in message
    assert "cli_test_app" not in message
    assert "test-app-secret" not in message


@pytest.mark.asyncio
async def test_feishu_client_classifies_http401_as_denied() -> None:
    client = FeishuClient(
        _credentials(),
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={})),
    )
    try:
        with pytest.raises(FeishuDeniedError):
            await client.health()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_feishu_client_http400_with_business_error_keeps_redacted_message() -> None:
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
            400,
            json={"code": 230002, "msg": "chat not found cli_test_app tenant-token"},
        )

    client = FeishuClient(_credentials(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ImError) as captured:
            await client.send_text(chat_id="oc_ops", text="hi", idempotency_key="run-1")
    finally:
        await client.aclose()
    message = str(captured.value)
    assert "230002" in message
    assert "cli_test_app" not in message
    assert "tenant-token" not in message
