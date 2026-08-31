from __future__ import annotations

import json

from fastapi.testclient import TestClient

from obsion_im.envelopes import parse_inbound


def _create_user(client: TestClient, suffix: str) -> str:
    created = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": f"im-{suffix}",
            "email": f"im-{suffix}@obsion.dev",
            "display_name": f"IM {suffix}",
            "attributes": {},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_feishu_envelope_ingests_as_the_bound_principal(client: TestClient) -> None:
    alice_id = _create_user(client, "feishu-ops")
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={"channel": "feishu", "sender_id": "ou_alice", "user_id": alice_id},
    )
    assert binding.status_code == 201, binding.text
    inbound = parse_inbound(
        "feishu",
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_alice"}},
                "message": {
                    "chat_id": "oc_ops",
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
            "sender_display": inbound.sender_display,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["principal_id"] == alice_id
    turns = client.get(f"/api/v1/threads/{response.json()['thread_id']}/turns")
    assert turns.json()[0]["created_by"] == alice_id


def test_dingtalk_nickname_envelope_still_requires_staff_id(client: TestClient) -> None:
    response = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "dingtalk",
            "sender_id": "Alice 花名",
            "conversation_id": "cid-ops",
            "text": "你好",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "unknown_im_sender"
