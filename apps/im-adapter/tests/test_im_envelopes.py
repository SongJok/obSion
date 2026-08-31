from __future__ import annotations

import json

import pytest

from obsion_im.config import ImError
from obsion_im.envelopes import UrlVerification, parse_inbound
from obsion_im.signatures import (
    canonical_event_body,
    dingtalk_signature,
    feishu_signature,
    verify_inbound_signature,
    wecom_signature,
)

FEISHU_EVENT = {
    "schema": "2.0",
    "header": {"event_type": "im.message.receive_v1", "event_id": "evt-1"},
    "event": {
        "sender": {"sender_id": {"open_id": "ou_alice", "user_id": "alice"}, "sender_type": "user"},
        "message": {
            "chat_id": "oc_ops",
            "message_type": "text",
            "content": json.dumps({"text": "你好"}, ensure_ascii=False),
        },
    },
}

DINGTALK_EVENT = {
    "senderStaffId": "staff-alice",
    "senderNick": "Alice 花名",
    "conversationId": "cid-ops",
    "text": {"content": "继续"},
}

WECOM_EVENT = {
    "FromUserName": "wecom-alice",
    "MsgType": "text",
    "Content": "报表",
    "ChatId": "wr_ops",
}


def test_feishu_envelope_uses_open_id_not_nickname() -> None:
    inbound = parse_inbound("feishu", FEISHU_EVENT)
    assert inbound.channel == "feishu"
    assert inbound.sender_id == "ou_alice"
    assert inbound.conversation_id == "oc_ops"
    assert inbound.text == "你好"
    assert inbound.sender_display is None


def test_feishu_url_verification_does_not_create_a_turn() -> None:
    inbound = parse_inbound("feishu", {"type": "url_verification", "challenge": "challenge-1"})
    assert isinstance(inbound, UrlVerification)
    assert inbound.challenge == "challenge-1"


def test_dingtalk_nickname_cannot_replace_staff_id() -> None:
    inbound = parse_inbound("dingtalk", DINGTALK_EVENT)
    assert inbound.sender_id == "staff-alice"
    assert inbound.sender_display == "Alice 花名"
    with pytest.raises(ImError, match="senderStaffId"):
        parse_inbound(
            "dingtalk",
            {"senderNick": "Alice 花名", "conversationId": "cid", "text": {"content": "hi"}},
        )


def test_wecom_json_and_xml_use_from_user_name() -> None:
    inbound = parse_inbound("wecom", WECOM_EVENT)
    assert inbound.sender_id == "wecom-alice"
    assert inbound.conversation_id == "wr_ops"
    xml = (
        "<xml><FromUserName><![CDATA[wecom-alice]]></FromUserName>"
        "<MsgType><![CDATA[text]]></MsgType><Content><![CDATA[报表]]></Content></xml>"
    )
    parsed = parse_inbound("wecom", xml)
    assert parsed.sender_id == "wecom-alice"
    assert parsed.text == "报表"
    assert parsed.conversation_id == "wecom-alice"


def test_wecom_ciphertext_fails_closed_without_aes_decrypt() -> None:
    with pytest.raises(ImError, match="OBSION_WECOM_ENCODING_AES_KEY"):
        parse_inbound("wecom", "<xml><Encrypt><![CDATA[cipher]]></Encrypt></xml>")
    with pytest.raises(ImError, match="OBSION_WECOM_ENCODING_AES_KEY"):
        parse_inbound("wecom", {"Encrypt": "cipher"})


def test_lark_alias_maps_to_feishu_namespace() -> None:
    inbound = parse_inbound("lark", FEISHU_EVENT)
    assert inbound.channel == "feishu"


def test_signed_feishu_envelope_rejects_a_bad_signature() -> None:
    wrapped = {
        "timestamp": "1710000000",
        "nonce": "n1",
        "signature": "deadbeef",
        "event": FEISHU_EVENT,
    }
    with pytest.raises(ImError, match="signature is invalid"):
        verify_inbound_signature("feishu", wrapped, secret="encrypt-key")


def test_signed_vendor_envelopes_accept_local_hmac() -> None:
    feishu_body = canonical_event_body({"event": FEISHU_EVENT})
    feishu = {
        "timestamp": "1710000000",
        "nonce": "n1",
        "signature": feishu_signature("1710000000", "n1", "encrypt-key", feishu_body),
        "event": FEISHU_EVENT,
    }
    verify_inbound_signature("feishu", feishu, secret="encrypt-key")
    assert parse_inbound("feishu", feishu).sender_id == "ou_alice"

    dingtalk = {
        "timestamp": "1710000000",
        "sign": dingtalk_signature("1710000000", "app-secret"),
        "event": DINGTALK_EVENT,
    }
    verify_inbound_signature("dingtalk", dingtalk, secret="app-secret")
    assert parse_inbound("dingtalk", dingtalk).sender_id == "staff-alice"

    wecom = {
        "timestamp": "1710000000",
        "nonce": "n2",
        "echostr": "echo",
        "msg_signature": wecom_signature("token", "1710000000", "n2", "echo"),
        "event": WECOM_EVENT,
    }
    verify_inbound_signature("wecom", wecom, secret="token")
    assert parse_inbound("wecom", wecom).sender_id == "wecom-alice"
