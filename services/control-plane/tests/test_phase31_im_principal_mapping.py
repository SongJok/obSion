from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.application.im_identity import ImIdentityService
from obsion.common.errors import AuthorizationError
from obsion.security.auth import get_principal
from obsion.security.identity import Principal


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


def _bind_sender(client: TestClient, *, sender_id: str, user_id: str) -> None:
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "development",
            "sender_id": sender_id,
            "user_id": user_id,
        },
    )
    assert binding.status_code == 201, binding.text


def _ingest(
    client: TestClient,
    *,
    sender_id: str,
    conversation_id: str,
    text: str = "你好",
) -> dict[str, str]:
    response = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "development",
            "sender_id": sender_id,
            "conversation_id": conversation_id,
            "text": text,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_im_ingest_creates_the_turn_as_the_bound_principal(client: TestClient) -> None:
    alice_id = _create_user(client, "alice")
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "development",
            "sender_id": "alice-stable",
            "user_id": alice_id,
        },
    )
    assert binding.status_code == 201, binding.text

    first = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "development",
            "sender_id": "alice-stable",
            "conversation_id": "ops-room",
            "text": "你好",
            "sender_display": "Alice 花名",
        },
    )
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["principal_id"] == alice_id
    assert body["sender_id"] == "alice-stable"
    thread_id = body["thread_id"]

    second = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "development",
            "sender_id": "alice-stable",
            "conversation_id": "ops-room",
            "text": "继续",
        },
    )
    assert second.status_code == 202, second.text
    assert second.json()["thread_id"] == thread_id
    assert second.json()["principal_id"] == alice_id

    turns = client.get(f"/api/v1/threads/{thread_id}/turns")
    assert turns.status_code == 200, turns.text
    created_by = [item["created_by"] for item in turns.json()]
    assert created_by == [alice_id, alice_id]

    session = client.get("/api/v1/auth/session")
    bot_id = session.json()["principal_id"]
    assert alice_id != bot_id
    assert bot_id not in created_by


def test_im_senders_have_owner_scoped_workspaces_and_access(client: TestClient) -> None:
    session = client.get("/api/v1/auth/session").json()
    organization_id = session["organization_id"]
    alice_id = _create_user(client, "isolated-alice")
    bob_id = _create_user(client, "isolated-bob")
    _bind_sender(client, sender_id="isolated-alice", user_id=alice_id)
    _bind_sender(client, sender_id="isolated-bob", user_id=bob_id)

    alice = _ingest(
        client,
        sender_id="isolated-alice",
        conversation_id="shared-vendor-conversation",
    )
    bob = _ingest(
        client,
        sender_id="isolated-bob",
        conversation_id="shared-vendor-conversation",
    )
    assert alice["workspace_id"] != bob["workspace_id"]
    assert alice["thread_id"] != bob["thread_id"]

    alice_principal = Principal(
        id=UUID(alice_id),
        organization_id=UUID(organization_id),
        external_id="im-isolated-alice",
        display_name="IM isolated-alice",
    )
    bob_principal = Principal(
        id=UUID(bob_id),
        organization_id=UUID(organization_id),
        external_id="im-isolated-bob",
        display_name="IM isolated-bob",
    )

    client.app.dependency_overrides[get_principal] = lambda: alice_principal
    try:
        own_threads = client.get(f"/api/v1/workspaces/{alice['workspace_id']}/threads")
        assert own_threads.status_code == 200, own_threads.text
        assert {item["id"] for item in own_threads.json()} == {alice["thread_id"]}
        assert client.get(f"/api/v1/workspaces/{bob['workspace_id']}/threads").status_code == 404
        assert client.get(f"/api/v1/threads/{bob['thread_id']}/turns").status_code == 404
        assert client.get(f"/api/v1/runs/{bob['run_id']}").status_code == 404
        assert client.get(f"/api/v1/runs/{bob['run_id']}/artifacts").status_code == 404
        assert client.get(f"/api/v1/runs/{bob['run_id']}/evidence").status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    client.app.dependency_overrides[get_principal] = lambda: bob_principal
    try:
        own_threads = client.get(f"/api/v1/workspaces/{bob['workspace_id']}/threads")
        assert own_threads.status_code == 200, own_threads.text
        assert {item["id"] for item in own_threads.json()} == {bob["thread_id"]}
        assert client.get(f"/api/v1/workspaces/{alice['workspace_id']}/threads").status_code == 404
        assert client.get(f"/api/v1/threads/{alice['thread_id']}/turns").status_code == 404
        assert client.get(f"/api/v1/runs/{alice['run_id']}").status_code == 404
        assert client.get(f"/api/v1/runs/{alice['run_id']}/artifacts").status_code == 404
        assert client.get(f"/api/v1/runs/{alice['run_id']}/evidence").status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_im_conversation_identity_uses_the_complete_id(client: TestClient) -> None:
    alice_id = _create_user(client, "long-conversation")
    _bind_sender(client, sender_id="long-conversation", user_id=alice_id)
    shared_prefix = "c" * 80
    first = _ingest(
        client,
        sender_id="long-conversation",
        conversation_id=f"{shared_prefix}-one",
    )
    second = _ingest(
        client,
        sender_id="long-conversation",
        conversation_id=f"{shared_prefix}-two",
    )
    repeated = _ingest(
        client,
        sender_id="long-conversation",
        conversation_id=f"{shared_prefix}-one",
        text="继续",
    )
    assert first["workspace_id"] == second["workspace_id"]
    assert first["thread_id"] != second["thread_id"]
    assert repeated["thread_id"] == first["thread_id"]


def test_unmapped_sender_is_fail_closed(client: TestClient) -> None:
    response = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "development",
            "sender_id": "nobody",
            "conversation_id": "ops-room",
            "text": "你好",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "unknown_im_sender"


def test_display_name_cannot_authorize_or_enter_the_identity_schema(client: TestClient) -> None:
    alice_id = _create_user(client, "nickname")
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "development",
            "sender_id": "stable-id",
            "user_id": alice_id,
            "display_name": "Alice",
        },
    )
    assert binding.status_code == 422
    assert binding.json()["code"] == "request_validation_failed"

    ingested = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "development",
            "sender_id": "stable-id",
            "conversation_id": "ops-room",
            "text": "你好",
            "display_name": "Alice",
        },
    )
    assert ingested.status_code == 422
    assert ingested.json()["code"] == "request_validation_failed"


def test_revoked_binding_cannot_ingest(client: TestClient) -> None:
    alice_id = _create_user(client, "revoked")
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "development",
            "sender_id": "revoked-stable",
            "user_id": alice_id,
        },
    )
    assert binding.status_code == 201, binding.text
    revoked = client.post(f"/api/v1/admin/im-bindings/{binding.json()['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["active"] is False

    ingested = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "development",
            "sender_id": "revoked-stable",
            "conversation_id": "ops-room",
            "text": "你好",
        },
    )
    assert ingested.status_code == 403
    assert ingested.json()["code"] == "unknown_im_sender"


def test_feishu_is_an_identity_namespace_not_a_vendor_client(client: TestClient) -> None:
    alice_id = _create_user(client, "feishu")
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "feishu",
            "sender_id": "ou_alice",
            "user_id": alice_id,
        },
    )
    assert binding.status_code == 201, binding.text
    ingested = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "feishu",
            "sender_id": "ou_alice",
            "conversation_id": "oc_ops",
            "text": "你好",
        },
    )
    assert ingested.status_code == 202, ingested.text
    assert ingested.json()["channel"] == "feishu"
    assert ingested.json()["principal_id"] == alice_id


@pytest.mark.asyncio
async def test_ingest_requires_im_delegate_permission() -> None:
    service = ImIdentityService(workspaces=None)  # type: ignore[arg-type]
    actor = Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="im-bot",
        display_name="IM bot",
        permissions=frozenset({"identity.write"}),
    )
    with pytest.raises(AuthorizationError) as captured:
        await service.ingest_message(
            None,  # type: ignore[arg-type]
            actor,
            channel="development",
            sender_id="alice-stable",
            conversation_id="ops-room",
            text="你好",
        )
    assert captured.value.code == "im_delegate_denied"
