from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

from obsion_im.config import ImError
from obsion_im.dingtalk import configure_stream_connection
from obsion_im.dingtalk_stream import ensure_stream_tls_trust_store
from obsion_im.inbox import InboxClient, InboxSettings

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StreamSettings:
    inbox: InboxSettings
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    corp_id: str
    app_id: str | None = None

    def __post_init__(self) -> None:
        if not self.app_key.strip() or not self.app_secret.strip() or not self.corp_id.strip():
            raise ImError("Stream 需要固定 APP_KEY、APP_SECRET 和 CORP_ID 安装上下文")


def _field(payload: dict[str, object], key: str, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ImError("Stream 消息字段缺失或无效")
    return value


def normalize_message(message: Any, settings: StreamSettings) -> dict[str, str]:
    payload = message.data
    if not isinstance(payload, dict) or payload.get("msgtype") not in ("text", "richText"):
        raise ImError("Stream 仅接收规范文本消息")
    # 安装来自受管启动配置；payload 的 installation/URL/token 永不参与路由。
    if payload.get("senderCorpId") != settings.corp_id:
        raise ImError("Stream 企业上下文不匹配")
    if payload.get("robotCode") != settings.app_key:
        raise ImError("Stream 应用上下文不匹配")
    if settings.app_id is not None and getattr(message.headers, "app_id", None) != settings.app_id:
        raise ImError("Stream 连接应用上下文不匹配")
    for key, expected in (("corpId", settings.corp_id), ("appKey", settings.app_key)):
        if key in payload and payload[key] != expected:
            raise ImError("Stream 安装上下文冲突")
    if "appId" in payload and payload["appId"] != settings.app_id:
        raise ImError("Stream 安装上下文冲突")
    # msgId 是官方机器人业务事件键；不使用可随重连改变的连接消息头键。
    event = _field(payload, "msgId" if "msgId" in payload else "messageId", 255)
    if "msgId" in payload and "messageId" in payload and payload["messageId"] != event:
        raise ImError("Stream 事件键冲突")
    kind = payload.get("conversationType")
    if kind not in ("1", "2"):
        raise ImError("Stream 会话类型无效")
    text = payload.get("text")
    if payload.get("msgtype") == "richText":
        content = payload.get("content")
        pieces = content.get("richText") if isinstance(content, dict) else None
        if not isinstance(pieces, list) or not 1 <= len(pieces) <= 128:
            raise ImError("Stream 富文本片段缺失或超限")
        parts: list[str] = []
        size = 0
        for piece in pieces:
            if (
                not isinstance(piece, dict)
                or piece.get("type", "text") != "text"
                or not isinstance(piece.get("text"), str)
                or any(key in piece for key in ("downloadCode", "picture", "file"))
            ):
                raise ImError("Stream 富文本包含不支持的非文本片段")
            size += len(piece["text"])
            if size > 32000:
                raise ImError("Stream 富文本超出长度限制")
            parts.append(piece["text"])
        text = {"content": "".join(parts)}
    if not isinstance(text, dict):
        raise ImError("Stream 文本缺失")
    return {
        "vendor_event_id": event,
        "sender_id": _field(payload, "senderStaffId", 255),
        "conversation_id": _field(payload, "conversationId", 512),
        "conversation_type": "direct" if kind == "1" else "group",
        "text": _field(text, "content", 32000),
    }


def load_stream_sdk() -> Any:
    try:
        return importlib.import_module("dingtalk_stream")
    except ImportError:
        raise ImError(
            "未安装官方 Stream SDK；请安装 obsion-im[stream]（dingtalk-stream==0.24.3）"
        ) from None


def create_stream_handler(
    sdk: Any, settings: StreamSettings, *, client_factory: Any = InboxClient
) -> Any:
    # 可选 SDK 在运行时导入且没有类型声明；仅此继承边界使用局部类型豁免。
    class DurableInboxHandler(sdk.CallbackHandler):  # type: ignore[misc]
        # 官方 CallbackHandler.raw_process 将此二元组转换为同 messageId 的 ACK。
        async def process(self, message: Any) -> tuple[int, str]:
            stage = "normalize"
            try:
                payload = normalize_message(message, settings)
                stage = "persist"
                async with asyncio.timeout(settings.inbox.timeout_seconds):
                    client = client_factory(settings.inbox)
                    try:
                        await client.receive(payload)
                    finally:
                        # 关闭也计入 ACK 总预算；清理故障不输出原始异常。
                        with contextlib.suppress(Exception):
                            async with asyncio.timeout(0.05):
                                await client.aclose()
            except Exception:
                # Only locally chosen codes; no payload, identifiers, tickets,
                # exception text or SDK objects may enter operational logs.
                _logger.warning("dingtalk.stream.inbox_rejected stage=%s", stage)
                return sdk.AckMessage.STATUS_SYSTEM_EXCEPTION, "Inbox 未确认持久接收"
            # 不调用 process，不等待 Turn/Run，不调用 SDK 回复方法。
            _logger.info("dingtalk.stream.inbox_accepted")
            return sdk.AckMessage.STATUS_OK, "Inbox 已持久接收"

    handler = DurableInboxHandler()
    handler.logger = _private_logger()
    return handler


def _private_logger() -> logging.Logger:
    # SDK 默认日志会输出连接 ticket、原始消息和异常；独立禁用日志且不传播。
    logger = logging.Logger("obsion_im.stream.sdk")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    logger.disabled = True
    return logger


def run_stream(settings: StreamSettings, *, sdk: Any = None) -> None:
    sdk = sdk if sdk is not None else load_stream_sdk()
    try:
        ensure_stream_tls_trust_store()
        client = sdk.DingTalkStreamClient(
            sdk.Credential(settings.app_key, settings.app_secret), logger=_private_logger()
        )
        configure_stream_connection(client)
        client.event_handler.logger = _private_logger()
        client.system_handler.logger = _private_logger()
        client.register_callback_handler(
            sdk.ChatbotMessage.TOPIC, create_stream_handler(sdk, settings)
        )
        # 0.24.3 无公开 stop，且吞掉 asyncio 取消；生产用进程监督器 SIGTERM 停止。
        client.start_forever()
    except KeyboardInterrupt:
        raise
    except Exception:
        raise ImError("Stream 生命周期失败；请检查受管配置，日志不含厂商原始响应") from None
