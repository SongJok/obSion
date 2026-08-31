from __future__ import annotations

import json
from pathlib import Path

import pytest

from obsion_im.channel import OutboundMessage
from obsion_im.config import (
    DINGTALK_HTTP_DELIVERY,
    FEISHU_HTTP_DELIVERY,
    WECOM_HTTP_DELIVERY,
    ImError,
    require_local_delivery,
)
from obsion_im.replies import persist_local_outbox, render_outbound


def _message(**overrides: object) -> OutboundMessage:
    payload = {
        "conversation_id": "oc_ops",
        "text": "根因已记录。",
        "run_id": "run-1",
        "thread_id": "thread-1",
        "channel": "feishu",
        "reply_to_sender_id": "ou_alice",
    }
    payload.update(overrides)
    return OutboundMessage(**payload)  # type: ignore[arg-type]


def test_http_delivery_is_rejected() -> None:
    with pytest.raises(ImError, match="HTTP delivery is not implemented"):
        require_local_delivery("http")
    with pytest.raises(ImError, match="HTTP delivery is not implemented"):
        render_outbound(_message(delivery="webhook"))


def test_feishu_outbound_uses_chat_id_and_stays_in_the_local_outbox() -> None:
    envelope = render_outbound(_message())
    assert envelope["delivery"] == "local_outbox"
    assert envelope["channel"] == "feishu"
    assert envelope["vendor"]["receive_id"] == "oc_ops"
    assert envelope["vendor"]["receive_id_type"] == "chat_id"
    assert envelope["vendor"]["msg_type"] == "text"
    assert json.loads(envelope["vendor"]["content"]) == {"text": "根因已记录。"}
    assert "http" not in json.dumps(envelope)


def test_feishu_http_outbound_uses_the_same_governed_envelope() -> None:
    envelope = render_outbound(_message(delivery=FEISHU_HTTP_DELIVERY))
    assert envelope["delivery"] == FEISHU_HTTP_DELIVERY
    assert envelope["channel"] == "feishu"
    assert envelope["vendor"]["receive_id"] == "oc_ops"
    with pytest.raises(ImError, match="requires the feishu channel"):
        render_outbound(_message(channel="dingtalk", delivery=FEISHU_HTTP_DELIVERY))


def test_dingtalk_and_wecom_http_outbound_reuse_vendor_envelopes() -> None:
    dingtalk = render_outbound(
        _message(
            channel="dingtalk",
            conversation_id="cid-ops",
            delivery=DINGTALK_HTTP_DELIVERY,
        )
    )
    assert dingtalk["delivery"] == DINGTALK_HTTP_DELIVERY
    assert dingtalk["vendor"]["conversation_id"] == "cid-ops"
    wecom = render_outbound(
        _message(
            channel="wecom",
            conversation_id="wr_ops",
            reply_to_sender_id="wecom-alice",
            delivery=WECOM_HTTP_DELIVERY,
        )
    )
    assert wecom["delivery"] == WECOM_HTTP_DELIVERY
    assert wecom["vendor"]["ChatId"] == "wr_ops"
    with pytest.raises(ImError, match="requires the dingtalk channel"):
        render_outbound(_message(channel="feishu", delivery=DINGTALK_HTTP_DELIVERY))
    with pytest.raises(ImError, match="requires the wecom channel"):
        render_outbound(_message(channel="feishu", delivery=WECOM_HTTP_DELIVERY))


def test_dingtalk_and_wecom_outbound_reuse_inbound_conversation_identity() -> None:
    dingtalk = render_outbound(
        _message(channel="dingtalk", conversation_id="cid-ops", reply_to_sender_id="staff-alice")
    )
    assert dingtalk["vendor"]["msgtype"] == "text"
    assert dingtalk["vendor"]["text"]["content"] == "根因已记录。"
    assert dingtalk["vendor"]["conversation_id"] == "cid-ops"
    wecom = render_outbound(
        _message(channel="wecom", conversation_id="wr_ops", reply_to_sender_id="wecom-alice")
    )
    assert wecom["vendor"]["ToUserName"] == "wecom-alice"
    assert wecom["vendor"]["ChatId"] == "wr_ops"
    assert wecom["vendor"]["MsgType"] == "text"


def test_lark_alias_renders_as_feishu_local_outbox() -> None:
    envelope = render_outbound(_message(channel="lark"))
    assert envelope["channel"] == "feishu"
    assert envelope["delivery"] == "local_outbox"


def test_local_outbox_file_appends_jsonl_and_rejects_urls(tmp_path: Path) -> None:
    path = tmp_path / "im-outbox.jsonl"
    first = render_outbound(_message(run_id="run-1"))
    second = render_outbound(_message(run_id="run-2", text="第二轮"))
    persist_local_outbox(path, first)
    persist_local_outbox(path, second)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "run-1"
    assert json.loads(lines[1])["text"] == "第二轮"
    with pytest.raises(ImError, match="local file path"):
        persist_local_outbox(Path("https://example.invalid/outbox"), first)
    with pytest.raises(ImError, match="non-local"):
        persist_local_outbox(path, {**first, "delivery": "http"})
