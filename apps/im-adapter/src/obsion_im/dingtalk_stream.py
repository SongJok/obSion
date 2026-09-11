"""Optional DingTalk Stream ingress adapter.

The SDK is intentionally imported only when a Stream client is started.  This
keeps the IM adapter installable for webhook and local-outbox use without a
third-party Stream runtime.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import math
import os
import ssl
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from obsion_im.channel import InboundMessage, OutboundMessage
from obsion_im.config import DingTalkCredentials, ImError
from obsion_im.envelopes import parse_inbound

_SDK_MODULE = "dingtalk_stream"
_MAX_EVENT_BYTES = 1_048_576
_EVENT_ID_KEYS = ("eventId", "event_id", "messageId", "message_id", "id", "msgId")
_INSTALLATION_ID_KEYS = (
    "installationId",
    "installation_id",
    "robotCode",
    "robot_code",
    "chatbotUserId",
)
_CORP_ID_KEYS = ("corpId", "corp_id", "chatbotCorpId", "senderCorpId")
_APP_KEY_KEYS = ("appKey", "app_key")
_UNIFIED_APP_ID_KEYS = (
    "unifiedAppId",
    "unified_app_id",
    "eventUnifiedAppId",
    "event_unified_app_id",
)
_ROBOT_CODE_KEYS = ("robotCode", "robot_code")
_logger = logging.getLogger(__name__)


class InboundBridge(Protocol):
    async def handle(self, inbound: InboundMessage) -> OutboundMessage: ...


SdkLoader = Callable[[], Any]


class DingTalkStreamAdapter:
    """Translate validated DingTalk Stream callbacks into the existing IM bridge."""

    def __init__(
        self,
        bridge: InboundBridge,
        credentials: DingTalkCredentials,
        *,
        sdk_loader: SdkLoader | None = None,
        ack_timeout_seconds: float = 0.8,
    ) -> None:
        if not math.isfinite(ack_timeout_seconds) or ack_timeout_seconds <= 0:
            raise ImError("Stream ACK timeout must be finite and positive")
        self._bridge = bridge
        self._credentials = credentials
        self._sdk_loader = sdk_loader or _load_sdk
        self._ack_timeout_seconds = ack_timeout_seconds

    async def handle_callback(self, callback: object) -> InboundMessage:
        inbound = parse_dingtalk_stream_event(callback, app_key=self._credentials.app_key)
        validate_dingtalk_application_identity(inbound, self._credentials)
        # ImBridge routes this event to durable admission, never the Run-wait path.
        async with asyncio.timeout(self._ack_timeout_seconds):
            await self._bridge.handle(inbound)
        return inbound

    def start_forever(self) -> None:
        """Start the vendor SDK after all optional SDK interfaces are verified."""
        ensure_stream_tls_trust_store()
        client, handler_type, ack_type, topic = self._prepare_client()
        start = _sdk_callable(client, "start_forever")
        start()

    async def start(self) -> None:
        """Start the vendor SDK without nesting ``asyncio.run`` in the CLI loop."""
        ensure_stream_tls_trust_store()
        client, _handler_type, _ack_type, _topic = self._prepare_client()
        async_start = getattr(client, "start", None)
        if callable(async_start):
            result = async_start()
            if inspect.isawaitable(result):
                await result
                return
        raise ImError("The installed DingTalk Stream SDK requires an asynchronous start method")

    def _prepare_client(self) -> tuple[Any, Any, Any, Any]:
        sdk = self._sdk_loader()
        credential_type = _sdk_callable(sdk, "Credential")
        client_type = _sdk_callable(sdk, "DingTalkStreamClient")
        handler_type = _sdk_callback_handler_type(sdk)
        ack_type = _sdk_callable(sdk, "AckMessage")
        topic = _sdk_chatbot_topic(sdk, handler_type)
        adapter = self

        class Handler(handler_type):  # type: ignore[misc, valid-type]
            async def process(self, callback: object) -> tuple[object, str]:
                try:
                    await adapter.handle_callback(callback)
                except Exception:
                    # Neither ACK nor SDK logs may echo raw vendor/control-plane errors.
                    return _sdk_value(ack_type, "STATUS_SYSTEM_EXCEPTION"), "Inbox not confirmed"
                return _sdk_value(ack_type, "STATUS_OK"), "OK"

        credential = credential_type(self._credentials.app_key, self._credentials.app_secret)
        client = client_type(credential, logger=_private_sdk_logger())
        for name in ("event_handler", "system_handler"):
            sdk_handler = getattr(client, name, None)
            if sdk_handler is not None:
                sdk_handler.logger = _private_sdk_logger()
        register = _sdk_callable(client, "register_callback_handler")
        handler = Handler()
        handler.logger = _private_sdk_logger()
        register(topic, handler)
        return client, handler_type, ack_type, topic


def parse_dingtalk_stream_event(callback: object, *, app_key: str) -> InboundMessage:
    """Validate a Stream callback and preserve its tenant-scoped event identity."""
    event, payload, headers = _callback_event(callback)
    return _parse_trusted_dingtalk_event(
        payload,
        headers=headers,
        event=event,
        app_key=app_key,
        source="Stream",
    )


def parse_dingtalk_http_event(
    payload: object,
    *,
    app_key: str,
    headers: Mapping[str, Any] | None = None,
    credentials: DingTalkCredentials | None = None,
) -> InboundMessage:
    """Validate a signed DingTalk HTTP callback for trusted Inbox admission.

    HTTP and Stream transports carry the same tenant identity into the Bridge.  The
    webhook has already verified the callback signature before calling this helper;
    this function deliberately performs no network or credential operation.
    """
    body = _payload_object(payload)
    request_headers = dict(headers) if isinstance(headers, Mapping) else {}
    inbound = _parse_trusted_dingtalk_event(
        body,
        headers=request_headers,
        event={"data": body, "headers": request_headers},
        app_key=app_key,
        source="HTTP",
    )
    if credentials is not None:
        validate_dingtalk_application_identity(inbound, credentials)
    return inbound


def validate_dingtalk_application_identity(
    inbound: InboundMessage,
    credentials: DingTalkCredentials,
) -> InboundMessage:
    """Verify optional configured identifiers against the trusted event."""
    event = inbound.vendor_event if isinstance(inbound.vendor_event, Mapping) else {}
    data: Mapping[str, Any] = _mapping(event.get("data"))
    headers: Mapping[str, Any] = _mapping(event.get("headers"))
    expected_unified = (credentials.unified_app_id or "").strip()
    expected_robot = (credentials.robot_code or "").strip()
    if expected_unified:
        # Chatbot callbacks may omit this event-only field. Any explicit value
        # must still match, including aliases in both headers and payload.
        for source in (headers, data):
            for key in _UNIFIED_APP_ID_KEYS:
                if key in source and source[key] != expected_unified:
                    raise ImError(
                        "DingTalk event unified application id does not match the configured robot"
                    )
    if expected_robot:
        actual = _required_identifier("robot code", data, headers, keys=_ROBOT_CODE_KEYS)
        if actual != expected_robot:
            raise ImError("DingTalk event robot code does not match the configured robot")
    return inbound


def _parse_trusted_dingtalk_event(
    payload: dict[str, Any],
    *,
    headers: dict[str, Any],
    event: dict[str, Any],
    app_key: str,
    source: str,
) -> InboundMessage:
    configured_app_key = app_key.strip()
    if not configured_app_key:
        raise ImError(f"DingTalk {source} requires a configured app key")
    # Transport message IDs can change on redelivery. Prefer the chatbot's
    # business key, shared with the installation-scoped Stream ingress.
    vendor_event_id = _first_identifier(payload, keys=("msgId",)) or _required_identifier(
        "vendor event id", headers, payload, event, keys=_EVENT_ID_KEYS
    )
    installation_id = _required_identifier(
        "installation id", payload, headers, keys=_INSTALLATION_ID_KEYS
    )
    corp_id = _required_identifier("corp id", payload, headers, keys=_CORP_ID_KEYS)
    event_app_key = _first_identifier(payload, headers, keys=_APP_KEY_KEYS)
    if event_app_key is not None and event_app_key != configured_app_key:
        raise ImError("DingTalk Stream event app key does not match the configured app key")
    normalized_payload = _normalize_chatbot_payload(payload)
    inbound = parse_inbound("dingtalk", normalized_payload)
    if not isinstance(inbound, InboundMessage):
        raise ImError("DingTalk Stream events cannot be URL verification callbacks")
    return replace(
        inbound,
        installation_id=installation_id,
        corp_id=corp_id,
        app_key=configured_app_key,
        vendor_event_id=vendor_event_id,
        vendor_event=event,
    )


def _private_sdk_logger() -> logging.Logger:
    logger = logging.Logger("obsion_im.dingtalk_stream.sdk")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.disabled = True
    return logger


def _load_sdk() -> Any:
    try:
        return importlib.import_module(_SDK_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == _SDK_MODULE:
            raise ImError(
                "DingTalk Stream requires the optional dingtalk-stream SDK package"
            ) from exc
        raise


def ensure_stream_tls_trust_store() -> None:
    """Use certifi only when this Python runtime has no default certificate roots.

    The vendor SDK creates its own WebSocket connection and does not share the
    HTTP client's TLS configuration.  Some packaged Python runtimes expose no
    default CA roots, while ``requests`` used by the same SDK does.  Preserve an
    operator-supplied ``SSL_CERT_FILE`` and ordinary system trust stores; only
    then install certifi's verified bundle for the SDK's WebSocket connection.
    """
    if os.environ.get("SSL_CERT_FILE") or _has_default_tls_authority():
        return
    bundle = Path(_certifi_ca_bundle())
    if not bundle.is_file():
        raise ImError("DingTalk Stream TLS trust bundle is unavailable")
    os.environ["SSL_CERT_FILE"] = str(bundle)


def _has_default_tls_authority() -> bool:
    try:
        return bool(ssl.create_default_context().get_ca_certs())
    except ssl.SSLError:
        return False


def _certifi_ca_bundle() -> str:
    import certifi

    return certifi.where()


def _sdk_callable(value: object, name: str) -> Any:
    attribute = getattr(value, name, None)
    if attribute is None or not callable(attribute):
        raise ImError("The installed DingTalk Stream SDK is incompatible")
    return attribute


def _sdk_value(value: object, name: str) -> Any:
    attribute = getattr(value, name, None)
    if attribute is None:
        raise ImError("The installed DingTalk Stream SDK is incompatible")
    return attribute


def _sdk_callback_handler_type(sdk: object) -> Any:
    chatbot_handler = getattr(sdk, "ChatbotHandler", None)
    if callable(chatbot_handler):
        return chatbot_handler
    return _sdk_callable(sdk, "CallbackHandler")


def _sdk_chatbot_topic(sdk: object, handler_type: object) -> Any:
    chatbot = getattr(sdk, "chatbot", None)
    chatbot_message = getattr(chatbot, "ChatbotMessage", None)
    if chatbot_message is not None:
        return _sdk_value(chatbot_message, "TOPIC")
    chatbot_message = getattr(sdk, "ChatbotMessage", None)
    if chatbot_message is not None:
        return _sdk_value(chatbot_message, "TOPIC")
    return _sdk_value(handler_type, "TOPIC")


def _callback_event(callback: object) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if isinstance(callback, Mapping):
        event = dict(callback)
        raw_payload = event.get("data", event)
        headers = _mapping(event.get("headers"))
    else:
        raw_payload = getattr(callback, "data", None)
        headers = _mapping(getattr(callback, "headers", None))
        if raw_payload is None:
            raise ImError("DingTalk Stream callback is missing event data")
        event = {"data": raw_payload, "headers": headers}
    payload = _payload_object(raw_payload)
    event["data"] = payload
    event["headers"] = headers
    return event, payload, headers


def _payload_object(value: object) -> dict[str, Any]:
    if isinstance(value, bytes):
        if len(value) > _MAX_EVENT_BYTES:
            raise ImError("DingTalk Stream event exceeded the size limit")
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ImError("DingTalk Stream event was not UTF-8") from exc
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_EVENT_BYTES:
            raise ImError("DingTalk Stream event exceeded the size limit")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ImError("DingTalk Stream event was not valid JSON") from exc
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception as exc:
            raise ImError("DingTalk Stream event object could not be converted") from exc
        if isinstance(converted, Mapping):
            return dict(converted)
    raise ImError("DingTalk Stream event must be a JSON object")


def _normalize_chatbot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the SDK and documented chatbot text shapes without logging content."""

    normalized = dict(payload)
    message_type = str(normalized.get("msgtype") or normalized.get("messageType") or "text")
    if message_type.casefold() != "text":
        return normalized
    text = _mapping_or_none(normalized.get("text"))
    if text is not None and _non_empty_string(text.get("content")):
        normalized["text"] = {"content": str(text["content"]).strip()}
        return normalized
    if _non_empty_string(normalized.get("text")):
        normalized["text"] = {"content": str(normalized["text"]).strip()}
        return normalized
    content = normalized.get("content")
    content_map = _mapping_or_none(content)
    if content_map is not None:
        content_value = content_map.get("content", content_map.get("text"))
    else:
        content_value = content
    if _non_empty_string(content_value):
        normalized["text"] = {"content": str(content_value).strip()}
        return normalized
    _logger.debug(
        "dingtalk.chatbot_payload_missing_text fields=%s types=%s",
        sorted(str(key) for key in normalized),
        {str(key): type(value).__name__ for key, value in normalized.items()},
    )
    return normalized


def _mapping_or_none(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception:
            return None
        if isinstance(converted, Mapping):
            return converted
    return None


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception:
            converted = None
        if isinstance(converted, Mapping):
            result = dict(converted)
            # dingtalk-stream's Headers.to_dict omits callback event fields.
            for attribute, key in (
                ("event_born_time", "eventBornTime"),
                ("event_corp_id", "eventCorpId"),
                ("event_id", "eventId"),
                ("event_type", "eventType"),
                ("event_unified_app_id", "eventUnifiedAppId"),
            ):
                item = getattr(value, attribute, None)
                if item is not None:
                    result[key] = item
            extensions = getattr(value, "extensions", None)
            if isinstance(extensions, Mapping):
                result.update(extensions)
            return result
    return {}


def _required_identifier(
    label: str,
    *sources: Mapping[str, Any],
    keys: tuple[str, ...],
) -> str:
    value = _first_identifier(*sources, keys=keys)
    if value is None:
        raise ImError(f"DingTalk Stream event requires a stable {label}")
    return value


def _first_identifier(*sources: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
