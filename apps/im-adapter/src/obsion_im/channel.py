from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from obsion_im.config import ImSettings


@dataclass(frozen=True, slots=True)
class InboundMessage:
    conversation_id: str
    text: str
    sender_id: str
    sender_display: str | None = None
    channel: str = "development"


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    conversation_id: str
    text: str
    run_id: str
    thread_id: str
    channel: str
    reply_to_sender_id: str | None = None
    delivery: str = "local_outbox"
    delivery_id: str | None = None


@dataclass(frozen=True, slots=True)
class ImDeliveryReceipt:
    vendor_message_id: str


class ImChannel(Protocol):
    name: str
    delivery: str

    async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt | None: ...

    async def health(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class DevelopmentImChannel:
    """Local outbox transport. This is not Feishu, DingTalk, or WeCom HTTP."""

    name = "development"
    delivery = "local_outbox"

    def __init__(self, outbox_path: Path | None = None) -> None:
        self.outbox: list[OutboundMessage] = []
        self.envelopes: list[dict[str, Any]] = []
        self.outbox_path = outbox_path

    async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt | None:
        from obsion_im.config import require_local_delivery
        from obsion_im.replies import persist_local_outbox, render_outbound

        require_local_delivery(message.delivery)
        envelope = render_outbound(message)
        self.outbox.append(message)
        self.envelopes.append(envelope)
        if self.outbox_path is not None:
            persist_local_outbox(self.outbox_path, envelope)
        return None

    async def health(self) -> dict[str, Any]:
        return {"channel": self.name, "delivery": self.delivery, "authenticated": True}

    async def aclose(self) -> None:
        return None


def create_im_channel(settings: ImSettings) -> ImChannel:
    from obsion_im.config import (
        DINGTALK_HTTP_DELIVERY,
        FEISHU_HTTP_DELIVERY,
        LOCAL_DELIVERY,
        WECOM_HTTP_DELIVERY,
        ImError,
    )

    if settings.delivery == LOCAL_DELIVERY:
        return DevelopmentImChannel(outbox_path=settings.outbox_path)
    if settings.delivery == FEISHU_HTTP_DELIVERY:
        from obsion_im.feishu import FeishuClient, FeishuHttpChannel

        feishu_credentials = settings.feishu_credentials
        if feishu_credentials is None:
            raise ImError("Feishu credentials were not resolved")
        return FeishuHttpChannel(FeishuClient(feishu_credentials))
    if settings.delivery == DINGTALK_HTTP_DELIVERY:
        from obsion_im.dingtalk import DingTalkClient, DingTalkHttpChannel

        dingtalk_credentials = settings.dingtalk_credentials
        if dingtalk_credentials is None:
            raise ImError("DingTalk credentials were not resolved")
        return DingTalkHttpChannel(DingTalkClient(dingtalk_credentials))
    if settings.delivery == WECOM_HTTP_DELIVERY:
        from obsion_im.wecom import WeComClient, WeComHttpChannel

        wecom_credentials = settings.wecom_credentials
        if wecom_credentials is None:
            raise ImError("WeCom credentials were not resolved")
        return WeComHttpChannel(WeComClient(wecom_credentials))
    raise ImError("Unsupported IM delivery transport")


def conversation_thread_title(channel: str, conversation_id: str) -> str:
    safe = conversation_id.strip().replace("\r", " ").replace("\n", " ")[:64] or "default"
    return f"im:{channel}:{safe}"
