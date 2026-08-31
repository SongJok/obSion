from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from obsion_im.channel import OutboundMessage
from obsion_im.config import (
    DINGTALK_HTTP_DELIVERY,
    FEISHU_HTTP_DELIVERY,
    IDENTITY_NAMESPACES,
    LOCAL_DELIVERY,
    WECOM_HTTP_DELIVERY,
    ImError,
    normalize_channel,
    reject_remote_outbox,
    require_local_delivery,
)

_HTTP_CHANNEL_BY_DELIVERY = {
    FEISHU_HTTP_DELIVERY: "feishu",
    DINGTALK_HTTP_DELIVERY: "dingtalk",
    WECOM_HTTP_DELIVERY: "wecom",
}


def render_outbound(message: OutboundMessage) -> dict[str, Any]:
    namespace = normalize_channel(message.channel)
    if namespace not in IDENTITY_NAMESPACES:
        raise ImError("IM channel is not a supported identity namespace")
    delivery = message.delivery.strip().lower().replace("-", "_")
    required_channel = _HTTP_CHANNEL_BY_DELIVERY.get(delivery)
    if required_channel is not None:
        if namespace != required_channel:
            transport = delivery.replace("_", "-")
            raise ImError(f"{transport} delivery requires the {required_channel} channel")
    else:
        delivery = require_local_delivery(delivery)
    return {
        "channel": namespace,
        "conversation_id": message.conversation_id,
        "delivery": delivery,
        "run_id": message.run_id,
        "text": message.text,
        "thread_id": message.thread_id,
        "vendor": _vendor_payload(namespace, message),
    }


def persist_local_outbox(path: Path, envelope: Mapping[str, Any]) -> None:
    reject_remote_outbox(path)
    if str(envelope.get("delivery") or "") != LOCAL_DELIVERY:
        raise ImError("Refusing to persist a non-local IM delivery")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    line = json.dumps(dict(envelope), ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _vendor_payload(namespace: str, message: OutboundMessage) -> dict[str, Any]:
    text = message.text
    conversation_id = message.conversation_id
    if namespace == "development":
        return {"conversation_id": conversation_id, "text": text}
    if namespace == "feishu":
        return {
            "content": json.dumps({"text": text}, ensure_ascii=False),
            "msg_type": "text",
            "receive_id": conversation_id,
            "receive_id_type": "chat_id",
        }
    if namespace == "dingtalk":
        return {
            "conversation_id": conversation_id,
            "msgtype": "text",
            "text": {"content": text},
        }
    sender = (message.reply_to_sender_id or conversation_id).strip()
    payload: dict[str, Any] = {
        "Content": text,
        "MsgType": "text",
        "ToUserName": sender,
    }
    if conversation_id and conversation_id != sender:
        payload["ChatId"] = conversation_id
    return payload
