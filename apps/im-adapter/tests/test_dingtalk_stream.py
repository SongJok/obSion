from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from obsion_im.bridge import ImBridge
from obsion_im.channel import InboundMessage, OutboundMessage
from obsion_im.config import DingTalkCredentials, ImError
from obsion_im.dingtalk_stream import (
    DingTalkStreamAdapter,
    ensure_stream_tls_trust_store,
    parse_dingtalk_http_event,
    parse_dingtalk_stream_event,
)

_CREDENTIALS = DingTalkCredentials(
    app_key="stream-app-key",
    app_secret="stream-app-secret",
)
_IDENTITY_CREDENTIALS = DingTalkCredentials(
    app_key="stream-app-key",
    app_secret="stream-app-secret",
    unified_app_id="unified-app-1",
    robot_code="robot-1",
)
_PAYLOAD = {
    "msgId": "vendor-event-1",
    "installationId": "installation-1",
    "chatbotCorpId": "corp-1",
    "appKey": "stream-app-key",
    "senderStaffId": "staff-alice",
    "senderNick": "Alice",
    "conversationId": "cid-ops",
    "text": {"content": "status"},
}


@dataclass
class _Callback:
    data: object
    headers: dict[str, object]


class _HeadersObject:
    def __init__(self) -> None:
        self.app_id = "stream-app-key"
        self.message_id = "header-event-1"
        self.event_id = "header-event-1"
        self.event_corp_id = "corp-1"
        self.event_unified_app_id = "unified-app-1"
        self.extensions = {"installationId": "installation-1"}

    def to_dict(self) -> dict[str, object]:
        return {"appId": self.app_id, "messageId": self.message_id}


@dataclass
class _ObjectCallback:
    data: object
    headers: object


def test_stream_event_preserves_full_event_and_tenant_identity() -> None:
    callback = _Callback(
        data=_PAYLOAD,
        headers={
            "eventId": "stream-event-1",
            "eventUnifiedAppId": "unified-app-1",
        },
    )

    inbound = parse_dingtalk_stream_event(callback, app_key=_CREDENTIALS.app_key)
    assert inbound == InboundMessage(
        conversation_id="cid-ops",
        text="status",
        sender_id="staff-alice",
        sender_display="Alice",
        channel="dingtalk",
        installation_id="installation-1",
        corp_id="corp-1",
        app_key="stream-app-key",
        vendor_event_id="vendor-event-1",
        vendor_event={
            "data": _PAYLOAD,
            "headers": {
                "eventId": "stream-event-1",
                "eventUnifiedAppId": "unified-app-1",
            },
        },
    )


def test_signed_http_event_preserves_the_same_trusted_identity() -> None:
    inbound = parse_dingtalk_http_event(
        {**_PAYLOAD, "robotCode": "robot-1"},
        app_key=_CREDENTIALS.app_key,
        headers={
            "eventId": "header-event-1",
            "eventUnifiedAppId": "unified-app-1",
        },
        credentials=_IDENTITY_CREDENTIALS,
    )

    assert inbound.installation_id == "installation-1"
    assert inbound.corp_id == "corp-1"
    assert inbound.app_key == _CREDENTIALS.app_key
    assert inbound.vendor_event_id == "vendor-event-1"
    assert inbound.vendor_event["headers"]["eventUnifiedAppId"] == "unified-app-1"


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("msgId", "vendor event id"),
        ("installationId", "installation id"),
        ("chatbotCorpId", "corp id"),
    ],
)
def test_signed_http_event_rejects_missing_trusted_identity(field: str, message: str) -> None:
    payload = {key: value for key, value in _PAYLOAD.items() if key != field}

    with pytest.raises(ImError, match=message):
        parse_dingtalk_http_event(payload, app_key=_CREDENTIALS.app_key)


def test_stream_event_accepts_the_sdk_headers_object() -> None:
    callback = _ObjectCallback(data={**_PAYLOAD, "msgId": None}, headers=_HeadersObject())

    inbound = parse_dingtalk_stream_event(callback, app_key=_CREDENTIALS.app_key)

    assert inbound.vendor_event_id == "header-event-1"
    assert inbound.corp_id == "corp-1"
    assert inbound.vendor_event["headers"]["eventId"] == "header-event-1"


def test_stream_event_accepts_real_sdk_event_identity_headers() -> None:
    from dingtalk_stream.frames import Headers

    headers = Headers()
    headers.event_id = "sdk-event-1"
    headers.event_corp_id = "corp-1"
    headers.event_unified_app_id = "unified-app-1"
    headers.extensions["robotCode"] = "robot-1"
    inbound = parse_dingtalk_stream_event(
        _ObjectCallback(data={**_PAYLOAD, "msgId": None}, headers=headers),
        app_key=_CREDENTIALS.app_key,
    )

    from obsion_im.dingtalk_stream import validate_dingtalk_application_identity

    validate_dingtalk_application_identity(inbound, _IDENTITY_CREDENTIALS)
    assert inbound.vendor_event_id == "sdk-event-1"
    assert inbound.corp_id == "corp-1"
    assert inbound.vendor_event["headers"]["eventUnifiedAppId"] == "unified-app-1"


@pytest.mark.parametrize(
    "content",
    ["status from content", {"content": "status from nested content"}],
)
def test_stream_event_normalizes_dingtalk_text_content_shapes(content: object) -> None:
    payload = {**_PAYLOAD, "text": None, "content": content}

    inbound = parse_dingtalk_stream_event(
        _Callback(data=payload, headers={"eventId": "content-event-1"}),
        app_key=_CREDENTIALS.app_key,
    )

    assert inbound.text.startswith("status from")


def test_stream_event_normalizes_sdk_text_object() -> None:
    class Text:
        def to_dict(self) -> dict[str, str]:
            return {"content": "status from sdk object"}

    payload = {**_PAYLOAD, "text": Text()}

    inbound = parse_dingtalk_stream_event(
        _Callback(data=payload, headers={"eventId": "sdk-text-event-1"}),
        app_key=_CREDENTIALS.app_key,
    )

    assert inbound.text == "status from sdk object"


def test_stream_event_rejects_content_without_text() -> None:
    payload = {**_PAYLOAD, "text": None, "content": {"image": "opaque"}}

    with pytest.raises(ImError, match="text content"):
        parse_dingtalk_stream_event(
            _Callback(data=payload, headers={"eventId": "no-text-event-1"}),
            app_key=_CREDENTIALS.app_key,
        )


@pytest.mark.parametrize(
    "field",
    ["eventUnifiedAppId", "robotCode"],
)
def test_dingtalk_application_identity_rejects_wrong_configured_robot(field: str) -> None:
    headers = {
        "eventId": "identity-event-1",
        "eventUnifiedAppId": "unified-app-1",
    }
    payload = {**_PAYLOAD, "robotCode": "robot-1"}
    if field == "eventUnifiedAppId":
        headers[field] = "other-unified-app"
    else:
        payload[field] = "other-robot"

    with pytest.raises(ImError, match="does not match"):
        parse_dingtalk_http_event(
            payload,
            app_key=_CREDENTIALS.app_key,
            headers=headers,
            credentials=_IDENTITY_CREDENTIALS,
        )


def test_unified_identity_can_be_absent_but_conflicting_alias_cannot_hide() -> None:
    payload = {**_PAYLOAD, "robotCode": "robot-1"}
    assert (
        parse_dingtalk_http_event(
            payload, app_key=_CREDENTIALS.app_key, credentials=_IDENTITY_CREDENTIALS
        ).vendor_event_id
        == "vendor-event-1"
    )
    with pytest.raises(ImError, match="does not match"):
        parse_dingtalk_http_event(
            {**payload, "unifiedAppId": "other-app"},
            headers={"eventUnifiedAppId": "unified-app-1"},
            app_key=_CREDENTIALS.app_key,
            credentials=_IDENTITY_CREDENTIALS,
        )


def test_business_event_id_survives_transport_redelivery_and_http_fallback() -> None:
    ids = {
        parse_dingtalk_stream_event(
            _Callback(data=_PAYLOAD, headers={"messageId": transport_id}),
            app_key=_CREDENTIALS.app_key,
        ).vendor_event_id
        for transport_id in ("connection-1", "connection-2")
    }
    ids.add(parse_dingtalk_http_event(_PAYLOAD, app_key=_CREDENTIALS.app_key).vendor_event_id)
    assert ids == {"vendor-event-1"}


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("msgId", "vendor event id"),
        ("installationId", "installation id"),
        ("chatbotCorpId", "corp id"),
    ],
)
def test_stream_event_requires_durable_tenant_scoped_identity(field: str, message: str) -> None:
    payload = {key: value for key, value in _PAYLOAD.items() if key != field}

    with pytest.raises(ImError, match=message):
        parse_dingtalk_stream_event(
            _Callback(data=payload, headers={}), app_key=_CREDENTIALS.app_key
        )


def test_stream_event_rejects_an_app_key_from_another_installation() -> None:
    payload = {**_PAYLOAD, "appKey": "other-app"}

    with pytest.raises(ImError, match="does not match"):
        parse_dingtalk_stream_event(
            _Callback(data=payload, headers={}), app_key=_CREDENTIALS.app_key
        )


def test_stream_tls_uses_certifi_when_python_has_no_default_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class EmptyContext:
        def get_ca_certs(self) -> list[object]:
            return []

    bundle = tmp_path / "certifi.pem"
    bundle.write_text("test certificate bundle", encoding="utf-8")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(
        "obsion_im.dingtalk_stream.ssl.create_default_context", lambda: EmptyContext()
    )
    monkeypatch.setattr("obsion_im.dingtalk_stream._certifi_ca_bundle", lambda: str(bundle))

    try:
        ensure_stream_tls_trust_store()

        assert os.environ["SSL_CERT_FILE"] == str(bundle)
    finally:
        os.environ.pop("SSL_CERT_FILE", None)


def test_stream_tls_preserves_operator_ca_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/operator/ca.pem")

    ensure_stream_tls_trust_store()

    assert os.environ["SSL_CERT_FILE"] == "/operator/ca.pem"


@pytest.mark.asyncio
async def test_stream_adapter_passes_the_complete_callback_to_the_bridge() -> None:
    class Bridge:
        inbound: InboundMessage | None = None

        async def handle(self, inbound: InboundMessage) -> OutboundMessage:
            self.inbound = inbound
            return OutboundMessage(
                conversation_id=inbound.conversation_id,
                text="ok",
                run_id="run-1",
                thread_id="thread-1",
                channel="dingtalk",
            )

    bridge = Bridge()
    callback = _Callback(data=_PAYLOAD, headers={"eventId": "stream-event-1"})

    inbound = await DingTalkStreamAdapter(bridge, _CREDENTIALS).handle_callback(callback)

    assert bridge.inbound is inbound
    assert inbound.vendor_event == {"data": _PAYLOAD, "headers": {"eventId": "stream-event-1"}}


@pytest.mark.asyncio
async def test_stream_sdk_is_loaded_only_when_starting_the_adapter() -> None:
    class Credential:
        def __init__(self, app_key: str, app_secret: str) -> None:
            self.app_key = app_key
            self.app_secret = app_secret

    class AckMessage:
        STATUS_OK = "OK"

    class CallbackHandler:
        pass

    class ChatbotHandler(CallbackHandler):
        pass

    class ChatbotMessage:
        TOPIC = "/v1.0/im/bot/messages/get"

    class Client:
        registered: tuple[str, object] | None = None
        started: bool = False

        def __init__(self, credential: Credential, *, logger: logging.Logger) -> None:
            self.credential = credential
            self.logger = logger

        def register_callback_handler(self, topic: str, handler: object) -> None:
            type(self).registered = (topic, handler)

        def start_forever(self) -> None:
            type(self).started = True

    class Bridge:
        async def handle(self, inbound: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                conversation_id=inbound.conversation_id,
                text="ok",
                run_id="run-1",
                thread_id="thread-1",
                channel="dingtalk",
            )

    sdk = type(
        "Sdk",
        (),
        {
            "Credential": Credential,
            "DingTalkStreamClient": Client,
            "CallbackHandler": CallbackHandler,
            "ChatbotHandler": ChatbotHandler,
            "AckMessage": AckMessage,
            "chatbot": type("Chatbot", (), {"ChatbotMessage": ChatbotMessage}),
        },
    )

    def load_sdk() -> object:
        return sdk

    adapter = DingTalkStreamAdapter(Bridge(), _CREDENTIALS, sdk_loader=load_sdk)

    adapter.start_forever()

    assert Client.started is True
    assert Client.registered is not None
    topic, handler = Client.registered
    assert topic == ChatbotMessage.TOPIC
    status, body = await cast(Any, handler).process(_Callback(data=_PAYLOAD, headers={}))
    assert (status, body) == (AckMessage.STATUS_OK, "OK")


@pytest.mark.asyncio
async def test_stream_async_start_prefers_sdk_async_start() -> None:
    class Credential:
        def __init__(self, app_key: str, app_secret: str) -> None:
            self.app_key = app_key
            self.app_secret = app_secret

    class AckMessage:
        STATUS_OK = "OK"

    class CallbackHandler:
        pass

    class ChatbotMessage:
        TOPIC = "topic"

    class Client:
        started = False
        sync_started = False

        def __init__(self, credential: Credential, *, logger: logging.Logger) -> None:
            self.credential = credential
            self.logger = logger

        def register_callback_handler(self, topic: str, handler: object) -> None:
            del topic, handler

        async def start(self) -> None:
            type(self).started = True

        def start_forever(self) -> None:
            type(self).sync_started = True
            raise AssertionError("nested sync start must not be used")

    sdk = type(
        "Sdk",
        (),
        {
            "Credential": Credential,
            "DingTalkStreamClient": Client,
            "CallbackHandler": CallbackHandler,
            "AckMessage": AckMessage,
            "chatbot": type("Chatbot", (), {"ChatbotMessage": ChatbotMessage}),
        },
    )

    class Bridge:
        async def handle(self, inbound: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                conversation_id=inbound.conversation_id,
                text="ok",
                run_id="run-1",
                thread_id="thread-1",
                channel="dingtalk",
            )

    adapter = DingTalkStreamAdapter(Bridge(), _CREDENTIALS, sdk_loader=lambda: sdk)
    await adapter.start()
    assert Client.started is True
    assert Client.sync_started is False


def test_stream_adapter_does_not_load_the_sdk_during_construction() -> None:
    class Bridge:
        async def handle(self, inbound: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                conversation_id=inbound.conversation_id,
                text="ok",
                run_id="run-1",
                thread_id="thread-1",
                channel="dingtalk",
            )

    def fail_if_loaded() -> Any:
        raise AssertionError("The optional SDK must not load during construction")

    DingTalkStreamAdapter(Bridge(), _CREDENTIALS, sdk_loader=fail_if_loaded)


def test_stream_sdk_missing_is_an_explicit_optional_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_sdk(_name: str) -> Any:
        raise ModuleNotFoundError("No module named 'dingtalk_stream'", name="dingtalk_stream")

    monkeypatch.setattr("obsion_im.dingtalk_stream.importlib.import_module", missing_sdk)

    class Bridge:
        async def handle(self, inbound: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                conversation_id=inbound.conversation_id,
                text="ok",
                run_id="run-1",
                thread_id="thread-1",
                channel="dingtalk",
            )

    adapter = DingTalkStreamAdapter(Bridge(), _CREDENTIALS)

    with pytest.raises(ImError, match="optional dingtalk-stream SDK"):
        adapter.start_forever()


@pytest.mark.asyncio
async def test_real_sdk_ack_waits_only_for_durable_admission_and_suppresses_logs(caplog) -> None:
    import dingtalk_stream as sdk

    entered = asyncio.Event()
    committed = asyncio.Event()

    async def accept(**kwargs):
        assert kwargs["vendor_event_id"] == "vendor-event-1"
        entered.set()
        await committed.wait()
        return {"inbox_event_id": "inbox-1"}

    runtime = SimpleNamespace(rest=SimpleNamespace(accept_trusted_im_event=accept))
    channel = SimpleNamespace(name="dingtalk", delivery="local_outbox", reply=AsyncMock())
    bridge = ImBridge(cast(Any, runtime), cast(Any, channel))
    adapter = DingTalkStreamAdapter(bridge, _CREDENTIALS, sdk_loader=lambda: sdk)
    client, _, _, topic = adapter._prepare_client()
    handler = client.callback_handler_map[topic]
    for owner in (client, client.event_handler, client.system_handler, handler):
        assert owner.logger.disabled and not owner.logger.propagate
        owner.logger.error("synthetic-sensitive-marker")
    task = asyncio.create_task(handler.process(_Callback(data=_PAYLOAD, headers={})))
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    committed.set()
    assert await task == (sdk.AckMessage.STATUS_OK, "OK")
    channel.reply.assert_not_awaited()
    assert "synthetic-sensitive-marker" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "exception", "receipt"])
async def test_real_sdk_admission_failure_returns_bounded_redacted_negative_ack(failure) -> None:
    import dingtalk_stream as sdk

    async def accept(**kwargs):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "exception":
            raise RuntimeError("synthetic-sensitive-marker")
        return {}  # Missing durable receipt must not become a successful ACK.

    runtime = SimpleNamespace(rest=SimpleNamespace(accept_trusted_im_event=accept))
    channel = SimpleNamespace(name="dingtalk", delivery="local_outbox", reply=AsyncMock())
    adapter = DingTalkStreamAdapter(
        ImBridge(cast(Any, runtime), cast(Any, channel)),
        _CREDENTIALS,
        sdk_loader=lambda: sdk,
        ack_timeout_seconds=0.01,
    )
    client, _, _, topic = adapter._prepare_client()
    handler = client.callback_handler_map[topic]
    callback = sdk.CallbackMessage()
    callback.data = _PAYLOAD
    callback.headers.message_id = "transport-1"
    ack = await asyncio.wait_for(handler.raw_process(callback), 1)
    assert ack.code == sdk.AckMessage.STATUS_SYSTEM_EXCEPTION
    assert ack.headers.message_id == "transport-1"
    assert ack.data == {"response": "Inbox not confirmed"}
    channel.reply.assert_not_awaited()
