from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from obsion_im.channel import InboundMessage
from obsion_im.config import IDENTITY_NAMESPACES, ImError, normalize_channel


@dataclass(frozen=True, slots=True)
class UrlVerification:
    challenge: str
    channel: str


Inbound = InboundMessage | UrlVerification


def parse_inbound(channel: str, payload: object) -> Inbound:
    namespace = normalize_channel(channel)
    if namespace not in IDENTITY_NAMESPACES:
        raise ImError("IM channel is not a supported identity namespace")
    if namespace == "wecom":
        reject_wecom_ciphertext(payload)
    if namespace == "development":
        return _parse_development(payload)
    if namespace == "feishu":
        return _parse_feishu(payload)
    if namespace == "dingtalk":
        return _parse_dingtalk(payload)
    return _parse_wecom(payload)


def reject_wecom_ciphertext(payload: object) -> None:
    """Reject ambiguous or undecryptable WeCom ciphertext envelopes."""
    if isinstance(payload, str):
        encrypt = _xml_field(payload, "Encrypt")
        content = _xml_field(payload, "Content")
        sender = _xml_field(payload, "FromUserName")
    elif isinstance(payload, dict):
        encrypt = str(payload.get("Encrypt") or payload.get("encrypt") or "").strip()
        content = str(payload.get("Content") or "").strip()
        sender = str(payload.get("FromUserName") or "").strip()
    else:
        return
    if not encrypt:
        return
    if content or sender:
        raise ImError("WeCom inbound must not mix Encrypt with plaintext message fields")
    raise ImError("WeCom AES ciphertext decrypt requires OBSION_WECOM_ENCODING_AES_KEY")


def _parse_development(payload: object) -> InboundMessage:
    body = _require_object(payload, "Development inbound")
    return InboundMessage(
        conversation_id=str(body.get("conversation_id") or ""),
        text=str(body.get("text") or ""),
        sender_id=str(body.get("sender_id") or ""),
        sender_display=_optional_text(body.get("sender_display")),
        channel="development",
    )


def _parse_feishu(payload: object) -> Inbound:
    body = _unwrap_signed(payload)
    if str(body.get("type") or "") == "url_verification":
        challenge = str(body.get("challenge") or "").strip()
        if not challenge:
            raise ImError("Feishu URL verification requires a challenge")
        return UrlVerification(challenge=challenge, channel="feishu")
    header = _object(body.get("header"))
    event = _object(body.get("event"))
    event_type = str(header.get("event_type") or body.get("event_type") or "")
    if event_type and event_type != "im.message.receive_v1":
        raise ImError("Feishu inbound only accepts im.message.receive_v1")
    message = _object(event.get("message") or body.get("message"))
    sender = _object(event.get("sender") or body.get("sender"))
    sender_ids = _object(sender.get("sender_id"))
    sender_id = str(
        sender_ids.get("open_id") or sender_ids.get("user_id") or sender.get("open_id") or ""
    ).strip()
    conversation_id = str(message.get("chat_id") or "").strip()
    text = _feishu_text(message)
    if not sender_id:
        raise ImError("Feishu inbound requires a stable open_id or user_id")
    if not conversation_id:
        raise ImError("Feishu inbound requires chat_id")
    if not text:
        raise ImError("Feishu inbound requires text message content")
    return InboundMessage(
        conversation_id=conversation_id,
        text=text,
        sender_id=sender_id,
        sender_display=None,
        channel="feishu",
    )


def _parse_dingtalk(payload: object) -> InboundMessage:
    body = _unwrap_signed(payload)
    sender_id = str(body.get("senderStaffId") or body.get("senderId") or "").strip()
    conversation_id = str(body.get("conversationId") or "").strip()
    text_block = body.get("text")
    text = ""
    if isinstance(text_block, dict):
        text = str(text_block.get("content") or "").strip()
    elif isinstance(text_block, str):
        text = text_block.strip()
    nickname = _optional_text(body.get("senderNick"))
    if not sender_id:
        raise ImError("DingTalk inbound requires senderStaffId. Nicknames cannot authorize.")
    if not conversation_id:
        raise ImError("DingTalk inbound requires conversationId")
    if not text:
        raise ImError("DingTalk inbound requires text content")
    return InboundMessage(
        conversation_id=conversation_id,
        text=text,
        sender_id=sender_id,
        sender_display=nickname,
        channel="dingtalk",
    )


def _parse_wecom(payload: object) -> Inbound:
    if isinstance(payload, dict) and str(payload.get("type") or "") == "url_verification":
        challenge = str(payload.get("challenge") or "").strip()
        if not challenge:
            raise ImError("WeCom URL verification requires a challenge")
        return UrlVerification(challenge=challenge, channel="wecom")
    if isinstance(payload, str) and payload.lstrip().startswith("<"):
        return _parse_wecom_xml(payload)
    body = _unwrap_signed(payload)
    sender_id = str(body.get("FromUserName") or "").strip()
    conversation_id = str(body.get("ChatId") or body.get("FromUserName") or "").strip()
    text = str(body.get("Content") or "").strip()
    msg_type = str(body.get("MsgType") or "text").strip().lower()
    if msg_type and msg_type != "text":
        raise ImError("WeCom inbound only accepts text messages")
    if not sender_id:
        raise ImError("WeCom inbound requires FromUserName")
    if not text:
        raise ImError("WeCom inbound requires Content")
    return InboundMessage(
        conversation_id=conversation_id,
        text=text,
        sender_id=sender_id,
        sender_display=None,
        channel="wecom",
    )


def _parse_wecom_xml(document: str) -> InboundMessage:
    msg_type = _xml_field(document, "MsgType").lower() or "text"
    if msg_type != "text":
        raise ImError("WeCom inbound only accepts text messages")
    sender_id = _xml_field(document, "FromUserName")
    text = _xml_field(document, "Content")
    conversation_id = _xml_field(document, "ChatId") or sender_id
    if not sender_id:
        raise ImError("WeCom inbound requires FromUserName")
    if not text:
        raise ImError("WeCom inbound requires Content")
    return InboundMessage(
        conversation_id=conversation_id,
        text=text,
        sender_id=sender_id,
        sender_display=None,
        channel="wecom",
    )


def _xml_field(document: str, tag: str) -> str:
    pattern = re.compile(
        rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(document)
    if match is None:
        return ""
    value = match.group(1).strip()
    if value.startswith("<![CDATA[") and value.endswith("]]>"):
        return value[9:-3].strip()
    return value


def _feishu_text(message: dict[str, Any]) -> str:
    message_type = str(message.get("message_type") or message.get("msg_type") or "text")
    if message_type != "text":
        raise ImError("Feishu inbound only accepts text messages")
    raw = message.get("content")
    if isinstance(raw, dict):
        return str(raw.get("text") or "").strip()
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if isinstance(parsed, dict):
        return str(parsed.get("text") or "").strip()
    return raw.strip()


def _unwrap_signed(payload: object) -> dict[str, Any]:
    body = _require_object(payload, "Vendor inbound")
    event = body.get("event")
    signed = any(
        key in body for key in ("timestamp", "nonce", "signature", "sign", "msg_signature")
    )
    if not isinstance(event, dict) or not signed:
        return body
    if any(
        key in event
        for key in ("schema", "header", "senderStaffId", "senderId", "FromUserName", "message")
    ):
        return event
    return body


def _require_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ImError(f"{label} must be a JSON object")
    return value


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
