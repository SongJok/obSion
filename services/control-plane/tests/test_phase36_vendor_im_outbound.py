from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from obsion_im.channel import OutboundMessage
from obsion_im.config import ImError, require_local_delivery
from obsion_im.envelopes import parse_inbound
from obsion_im.replies import render_outbound


def _create_user(client: TestClient, suffix: str) -> str:
    created = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": f"im-out-{suffix}",
            "email": f"im-out-{suffix}@obsion.dev",
            "display_name": f"IM out {suffix}",
            "attributes": {},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_feishu_outbound_envelope_stays_in_the_local_outbox(client: TestClient) -> None:
    alice_id = _create_user(client, "feishu")
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={"channel": "feishu", "sender_id": "ou_outbound", "user_id": alice_id},
    )
    assert binding.status_code == 201, binding.text
    inbound = parse_inbound(
        "feishu",
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_outbound"}},
                "message": {
                    "chat_id": "oc_outbound",
                    "message_type": "text",
                    "content": json.dumps({"text": "你好"}, ensure_ascii=False),
                },
            },
        },
    )
    response = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": inbound.channel,
            "sender_id": inbound.sender_id,
            "conversation_id": inbound.conversation_id,
            "text": inbound.text,
        },
    )
    assert response.status_code == 202, response.text
    envelope = render_outbound(
        OutboundMessage(
            conversation_id=inbound.conversation_id,
            text="已记录。",
            run_id=response.json()["run_id"],
            thread_id=response.json()["thread_id"],
            channel=inbound.channel,
            reply_to_sender_id=inbound.sender_id,
        )
    )
    assert envelope["delivery"] == "local_outbox"
    assert envelope["vendor"]["receive_id"] == "oc_outbound"
    assert response.json()["principal_id"] == alice_id
    with pytest.raises(ImError, match="HTTP delivery is not implemented"):
        require_local_delivery("http")
