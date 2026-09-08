from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from obsion_im.config import ImError
from obsion_im.inbox import InboxClient, InboxSettings
from obsion_im.main import main
from obsion_im.stream import (
    StreamSettings,
    create_stream_handler,
    load_stream_sdk,
    normalize_message,
    run_stream,
)

INSTALLATION = UUID(int=100)
SETTINGS = StreamSettings(
    InboxSettings(INSTALLATION, "http://127.0.0.1:8080", "cp-secret"),
    "app-key",
    "app-secret",
    "corp",
    "app-header",
)


class CallbackHandler:
    def __init__(self):
        self.logger = None


SDK = SimpleNamespace(
    CallbackHandler=CallbackHandler,
    AckMessage=SimpleNamespace(STATUS_OK=200, STATUS_SYSTEM_EXCEPTION=500),
)


def message(**changes):
    payload = {
        "msgtype": "text",
        "msgId": "stable-event",
        "senderStaffId": "staff",
        "conversationId": "conversation",
        "conversationType": "1",
        "senderCorpId": "corp",
        "robotCode": "app-key",
        "text": {"content": "正文-secret"},
        "sessionWebhook": "https://untrusted.invalid/token-secret",
        "sessionWebhookExpiredTime": "token-secret",
        "token": "payload-secret",
        "installation_id": str(UUID(int=999)),
        "url": "https://untrusted.invalid",
    }
    payload.update(changes)
    return SimpleNamespace(
        data=payload, headers=SimpleNamespace(app_id="app-header", message_id="frame")
    )


def receipt(event="stable-event", **changes):
    value = {
        "id": str(UUID(int=1)),
        "installation_id": str(INSTALLATION),
        "vendor_event_id": event,
        "status": "RECEIVED",
        "turn_id": None,
        "run_id": None,
        "created_at": "2026-09-06T00:00:00Z",
        "processed_at": None,
    }
    value.update(changes)
    return value


def handler(transport):
    return create_stream_handler(
        SDK, SETTINGS, client_factory=lambda settings: InboxClient(settings, transport=transport)
    )


@pytest.mark.asyncio
async def test_ack_only_after_commit_without_waiting_for_run_and_duplicate_same_key():
    requests = []
    committed = asyncio.Event()
    release = asyncio.Event()

    async def service(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/experience/im/installations/{INSTALLATION}/inbox"
        assert request.headers["authorization"] == "Bearer cp-secret"
        body = json.loads(request.content)
        assert set(body) == {
            "vendor_event_id",
            "sender_id",
            "conversation_id",
            "conversation_type",
            "text",
        }
        assert body["vendor_event_id"] == "stable-event"
        committed.set()
        await release.wait()
        return httpx.Response(202, json=receipt())

    callback = handler(httpx.MockTransport(service))
    task = asyncio.create_task(callback.process(message()))
    await committed.wait()
    assert not task.done()
    release.set()
    assert (await asyncio.wait_for(task, 0.2))[0] == 200
    replay = message()
    replay.headers.message_id = "new-frame-after-reconnect"
    assert (await callback.process(replay))[0] == 200
    assert requests[0].content == requests[1].content
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (500, {"secret": "server-secret"}),
        (200, receipt()),
        (302, receipt()),
        (202, {}),
        (202, receipt(installation_id=str(UUID(int=99)))),
        (202, receipt(event="other")),
        (202, receipt(text="leak")),
        (202, receipt(status="PROCESSED")),
        (202, receipt(created_at="bad")),
    ],
)
async def test_failed_or_invalid_persistence_never_acks_success(status, body, caplog):
    callback = handler(httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    result = await callback.process(message())
    assert result[0] == 500
    assert "secret" not in str(result) + caplog.text


@pytest.mark.asyncio
async def test_service_uncertainty_is_redacted(caplog):
    def service(request):
        raise httpx.ReadTimeout("token-secret 正文-secret", request=request)

    result = await handler(httpx.MockTransport(service)).process(message())
    assert result[0] == 500
    assert "secret" not in str(result) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"senderCorpId": "forged"},
        {"robotCode": "forged"},
        {"corpId": "forged"},
        {"appId": "forged"},
        {"msgId": None},
        {"msgId": "x" * 256},
        {"senderStaffId": None},
        {"senderStaffId": "x" * 256},
        {"conversationId": "x" * 513},
        {"conversationType": "group"},
        {"conversationType": 1},
        {"text": {}},
        {"text": {"content": "x" * 32001}},
        {"msgtype": "image"},
        {"messageId": "conflicting-event"},
    ],
)
async def test_forged_or_missing_fields_never_reach_control_plane(changes, caplog):
    def forbidden(request):
        pytest.fail("无效消息不应到达控制面")

    assert (await handler(httpx.MockTransport(forbidden)).process(message(**changes)))[0] == 500
    assert "secret" not in caplog.text


def test_message_id_alias_and_group_and_header_guard():
    item = message(conversationType="2")
    del item.data["msgId"]
    item.data["messageId"] = "stable-event"
    assert normalize_message(item, SETTINGS)["conversation_type"] == "group"
    item.headers.app_id = "forged"
    with pytest.raises(ImError, match="上下文"):
        normalize_message(item, SETTINGS)


def test_missing_optional_sdk_is_clear(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError("secret-package-path")

    monkeypatch.setattr("obsion_im.stream.importlib.import_module", missing)
    with pytest.raises(ImError, match=r"obsion-im\[stream\]") as exc:
        load_stream_sdk()
    assert "secret-package-path" not in str(exc.value)


def test_official_lifecycle_interface_without_real_connection(caplog):
    calls = []

    class Client:
        def __init__(self, credential, logger):
            calls.append(credential)
            self.logger = logger
            self.event_handler = SimpleNamespace(logger=None)
            self.system_handler = SimpleNamespace(logger=None)

        def register_callback_handler(self, topic, callback):
            assert topic == "/v1.0/im/bot/messages/get"
            assert isinstance(callback, CallbackHandler)
            self.logger.error("ticket-secret")
            self.event_handler.logger.error("event-secret")
            self.system_handler.logger.error("payload-secret")

        def start_forever(self):
            calls.append("start")

    fake = SimpleNamespace(
        **vars(SDK),
        DingTalkStreamClient=Client,
        Credential=lambda key, secret: (key, secret),
        ChatbotMessage=SimpleNamespace(TOPIC="/v1.0/im/bot/messages/get"),
    )
    run_stream(SETTINGS, sdk=fake)
    assert calls == [("app-key", "app-secret"), "start"]
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "extra,env",
    [
        (["--deliver", "local-outbox"], {}),
        (["--outbox", "local.jsonl"], {}),
        ([], {"OBSION_IM_DELIVER": "local-outbox"}),
        ([], {"OBSION_IM_OUTBOX": "local.jsonl"}),
    ],
)
def test_stream_rejects_delivery_configuration(extra, env):
    err = io.StringIO()
    assert (
        main(
            ["--channel", "dingtalk", *extra, "stream", "--installation-id", str(INSTALLATION)],
            environ=env,
            out=io.StringIO(),
            err=err,
        )
        == 1
    )
    assert "不支持" in err.getvalue()


def test_stream_cli_fixed_installation_from_environment(monkeypatch):
    calls = []
    monkeypatch.setattr("obsion_im.stream.run_stream", calls.append)
    env = {
        "OBSION_TOKEN": "cp-secret",
        "OBSION_IM_INSTALLATION_ID": str(INSTALLATION),
        "OBSION_DINGTALK_APP_KEY": "app-key",
        "OBSION_DINGTALK_APP_SECRET": "app-secret",
        "OBSION_DINGTALK_CORP_ID": "corp",
    }
    assert (
        main(["--channel", "dingtalk", "stream"], environ=env, out=io.StringIO(), err=io.StringIO())
        == 0
    )
    assert calls[0].inbox.installation_id == INSTALLATION


@pytest.mark.asyncio
async def test_receive_overall_budget_does_not_ack_success():
    from dataclasses import replace

    settings = replace(SETTINGS, inbox=replace(SETTINGS.inbox, timeout_seconds=0.01))

    async def slow_service(request):
        await asyncio.Event().wait()

    callback = create_stream_handler(
        SDK,
        settings,
        client_factory=lambda value: InboxClient(
            value, transport=httpx.MockTransport(slow_service)
        ),
    )
    assert (await asyncio.wait_for(callback.process(message()), 0.2))[0] == 500


@pytest.mark.asyncio
async def test_processed_duplicate_is_valid_commit_receipt():
    value = receipt(
        status="PROCESSED",
        turn_id=str(UUID(int=2)),
        run_id=str(UUID(int=3)),
        processed_at="2026-09-06T00:00:01Z",
    )
    callback = handler(httpx.MockTransport(lambda request: httpx.Response(202, json=value)))
    assert (await callback.process(message()))[0] == 200


@pytest.mark.asyncio
async def test_official_sdk_raw_process_offline_when_extra_installed():
    sdk = pytest.importorskip("dingtalk_stream", reason="可选官方 Stream extra 未安装")
    callback = create_stream_handler(
        sdk,
        SETTINGS,
        client_factory=lambda value: InboxClient(
            value,
            transport=httpx.MockTransport(lambda request: httpx.Response(202, json=receipt())),
        ),
    )
    frame = sdk.CallbackMessage.from_dict(
        {
            "type": "CALLBACK",
            "headers": {"messageId": "official-frame", "appId": "app-header"},
            "data": json.dumps(message().data),
        }
    )
    ack = await callback.raw_process(frame)
    assert ack.code == sdk.AckMessage.STATUS_OK
    assert ack.headers.message_id == "official-frame"
    assert "secret" not in json.dumps(ack.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,expected", [(0.01, 500), (0.2, 200)])
async def test_close_is_bounded_and_part_of_ack_budget(budget, expected):
    from dataclasses import replace

    closed = asyncio.Event()

    class SlowCloseClient:
        async def receive(self, payload):
            return receipt()

        async def aclose(self):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

    settings = replace(SETTINGS, inbox=replace(SETTINGS.inbox, timeout_seconds=budget))
    callback = create_stream_handler(SDK, settings, client_factory=lambda _: SlowCloseClient())
    assert (await asyncio.wait_for(callback.process(message()), 0.15))[0] == expected
    assert closed.is_set()
