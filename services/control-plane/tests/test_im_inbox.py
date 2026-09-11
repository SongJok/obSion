import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from obsion.application.im_inbox import _installation_connector_allowed
from obsion.common.time import utc_now
from obsion.db.im_models import ImConversationBinding, ImInboxMessage
from obsion.db.models import (
    AuditRecord,
    Connector,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
    WorkspaceMember,
)
from obsion.domain.enums import Visibility
from obsion.main import create_app
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

ADMIN = "/api/v1/admin/im-installations"
MESSAGE = {
    "vendor_event_id": "event-1",
    "sender_id": "sender-1",
    "conversation_id": "room-1",
    "conversation_type": "group",
    "text": "你好",
}


def provision(client: TestClient, suffix: str = "one", *, robot: bool = False) -> dict[str, Any]:
    auth = client.get("/api/v1/auth/session").json()
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": f"im-inbox-{suffix}",
            "connector_type": "dingtalk-robot" if robot else "dingtalk-docs",
            **(
                {
                    "endpoint": "https://api.dingtalk.com",
                    "configuration": {"protocol": "dingtalk.robot.oto.v1"},
                    "credential_ref": "env://OBSION_TEST_ROBOT_SECRET",
                    "declared_grants": ["im.reply.deliver"],
                    "allowed_egress": ["https://api.dingtalk.com"],
                }
                if robot
                else {}
            ),
            "environment": "test",
            "status": "ACTIVE",
        },
    )
    assert connector.status_code == 201, connector.text
    installation = client.post(
        ADMIN,
        json={
            "provider": "dingtalk",
            "external_corp_id": f"corp-{suffix}",
            "external_app_id": "app-1",
            "connector_id": connector.json()["id"],
            "adapter_principal_id": auth["principal_id"],
            "verification_source": "operator-verified-test-fixture",
        },
    )
    assert installation.status_code == 201, installation.text
    value = installation.json()
    binding = client.post(
        f"{ADMIN}/{value['id']}/bindings",
        json={
            "sender_id": "sender-1",
            "user_id": auth["principal_id"],
        },
    )
    assert binding.status_code == 201, binding.text
    return {
        **value,
        "binding_id": binding.json()["id"],
        "auth": auth,
        "url": f"/api/v1/experience/im/installations/{value['id']}/inbox",
    }


def test_robot_connector_can_admit_and_process_durable_message(client: TestClient) -> None:
    installation = provision(client, robot=True)
    response = client.post(installation["url"], json={**MESSAGE, "conversation_type": "direct"})
    assert response.status_code == 202, response.text
    processed = client.post(f"{installation['url']}/{response.json()['id']}/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "PROCESSED"
    assert counts(client) == (1, 1, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"connector_type": "http"},
        {"configuration": {}},
        {"endpoint": "https://example.invalid"},
        {"allowed_egress": []},
        {"declared_grants": []},
        {"credential_ref": None},
    ],
)
def test_robot_installation_rejects_incomplete_connection(changes: dict[str, Any]) -> None:
    connector = Connector(
        **{
            "connector_type": "dingtalk-robot",
            "configuration": {"protocol": "dingtalk.robot.oto.v1"},
            "endpoint": "https://api.dingtalk.com",
            "allowed_egress": ["https://api.dingtalk.com"],
            "declared_grants": ["im.reply.deliver"],
            "credential_ref": "env://OBSION_TEST_ROBOT_SECRET",
            **changes,
        }
    )
    assert not _installation_connector_allowed(connector, "dingtalk")
    assert not _installation_connector_allowed(connector, "feishu")


def counts(client: TestClient) -> tuple[int, int, int]:
    async def read() -> tuple[int, int, int]:
        async with client.app.state.database.sessions() as session:
            return (
                int(await session.scalar(select(func.count()).select_from(ImInboxMessage)) or 0),
                int(await session.scalar(select(func.count()).select_from(Turn)) or 0),
                int(await session.scalar(select(func.count()).select_from(Run)) or 0),
            )

    return asyncio.run(read())


def test_durable_ack_unique_process_and_restart(client: TestClient) -> None:
    installation = provision(client)
    response = client.post(installation["url"], json=MESSAGE)
    assert response.status_code == 202, response.text
    received = response.json()
    assert received["status"] == "RECEIVED"
    assert received["turn_id"] is None and received["run_id"] is None
    assert "text" not in received
    assert counts(client) == (1, 0, 0)  # Independent connection sees committed ACK.
    url = f"{installation['url']}/{received['id']}"
    assert client.get(url).json() == received
    processed = client.post(url + "/process")
    assert processed.status_code == 200, processed.text
    assert processed.json()["status"] == "PROCESSED"
    assert counts(client) == (1, 1, 1)
    assert client.post(url + "/process").json() == processed.json()
    with TestClient(
        create_app(client.app.state.settings), headers=dict(client.headers)
    ) as restarted:
        assert restarted.post(installation["url"], json=MESSAGE).json() == processed.json()
        assert restarted.post(url + "/process").json() == processed.json()
    assert counts(client) == (1, 1, 1)


def test_admin_reconciliation_requires_an_accepted_outbox(client: TestClient) -> None:
    response = client.post(f"/api/v1/admin/im-dingtalk/outbox/{uuid4()}/reconcile")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "im_delivery_receipt_conflict"


def test_hundred_concurrent_duplicate_receipts_and_processing(client: TestClient) -> None:
    installation = provision(client)

    def receive(_: int) -> dict[str, Any]:
        response = client.post(installation["url"], json=MESSAGE)
        assert response.status_code == 202, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=10) as pool:
        receipts = list(pool.map(receive, range(100)))
    assert len({receipt["id"] for receipt in receipts}) == 1
    assert counts(client) == (1, 0, 0)
    url = f"{installation['url']}/{receipts[0]['id']}/process"

    def process(_: int) -> dict[str, Any]:
        response = client.post(url)
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(process, range(20)))
    assert len({result["run_id"] for result in results}) == 1
    assert counts(client) == (1, 1, 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "不同内容"),
        ("sender_id", "another"),
        ("conversation_id", "other"),
        ("conversation_type", "direct"),
    ],
)
def test_content_conflict_is_durable_and_audited(
    client: TestClient,
    field: str,
    value: str,
) -> None:
    installation = provision(client)
    assert client.post(installation["url"], json=MESSAGE).status_code == 202
    conflict = client.post(installation["url"], json={**MESSAGE, field: value})
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "idempotency_key_reused"

    async def read() -> int:
        async with client.app.state.database.sessions() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(AuditRecord)
                    .where(AuditRecord.action == "im.inbox.content_conflict")
                )
                or 0
            )

    assert asyncio.run(read()) == 1
    assert counts(client) == (1, 0, 0)


@pytest.mark.parametrize(
    "field",
    [
        "organization_id",
        "installation_id",
        "provider",
        "roles",
        "principal_id",
        "fingerprint",
        "adapter_principal_id",
    ],
)
def test_payload_cannot_grant_authority(client: TestClient, field: str) -> None:
    installation = provision(client)
    response = client.post(installation["url"], json={**MESSAGE, field: str(uuid4())})
    assert response.status_code == 422, response.text
    assert counts(client) == (0, 0, 0)


def test_installation_and_adapter_isolation(client: TestClient) -> None:
    first = provision(client)
    second = provision(client, "two")
    receipt = client.post(first["url"], json=MESSAGE).json()
    assert client.get(f"{second['url']}/{receipt['id']}").status_code == 404
    assert client.post(f"{second['url']}/{receipt['id']}/process").status_code == 404
    other = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "other-adapter",
            "email": "other@obsion.dev",
            "display_name": "Other",
        },
    ).json()
    principal = Principal(
        id=UUID(other["id"]),
        organization_id=UUID(first["organization_id"]),
        external_id="other-adapter",
        display_name="Other",
        permissions=frozenset({"*"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: principal
    try:
        assert client.post(first["url"], json=MESSAGE).status_code == 403
        assert client.post(f"{first['url']}/{receipt['id']}/process").status_code == 403
        assert (
            client.post(
                ADMIN,
                json={
                    key: first[key]
                    for key in (
                        "provider",
                        "external_corp_id",
                        "external_app_id",
                        "connector_id",
                        "adapter_principal_id",
                        "verification_source",
                    )
                },
            ).status_code
            == 403
        )  # Forged cached permissions cannot authorize.
    finally:
        client.app.dependency_overrides.clear()
    foreign = Principal(
        id=principal.id,
        organization_id=uuid4(),
        external_id="other",
        display_name="Other",
        permissions=frozenset({"*"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: foreign
    try:
        assert client.post(first["url"], json=MESSAGE).status_code == 404
    finally:
        client.app.dependency_overrides.clear()


@pytest.mark.parametrize("revoke", ["installation", "binding", "subject", "replacement"])
def test_processing_rechecks_authorization(client: TestClient, revoke: str) -> None:
    installation = provision(client)
    receipt = client.post(installation["url"], json=MESSAGE).json()
    if revoke == "installation":
        assert client.post(f"{ADMIN}/{installation['id']}/revoke").status_code == 200
    elif revoke == "binding":
        assert (
            client.post(
                f"{ADMIN}/{installation['id']}/bindings/{installation['binding_id']}/revoke"
            ).status_code
            == 200
        )
    elif revoke == "replacement":
        user = client.post(
            "/api/v1/admin/users",
            json={
                "external_id": "replacement",
                "email": "replacement@obsion.dev",
                "display_name": "Replacement",
            },
        ).json()
        assert (
            client.post(
                f"{ADMIN}/{installation['id']}/bindings",
                json={
                    "sender_id": "sender-1",
                    "user_id": user["id"],
                },
            ).status_code
            == 201
        )
    else:

        async def disable() -> None:
            async with client.app.state.database.sessions() as session, session.begin():
                user = await session.get(User, UUID(installation["auth"]["principal_id"]))
                assert user is not None
                user.active = False

        asyncio.run(disable())
    response = client.post(f"{installation['url']}/{receipt['id']}/process")
    assert response.status_code == 403, response.text
    assert counts(client) == (1, 0, 0)


def test_task_creation_failure_rolls_back_then_replays(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = provision(client)
    receipt = client.post(installation["url"], json=MESSAGE).json()
    url = f"{installation['url']}/{receipt['id']}/process"
    original = client.app.state.workspace_service.create_turn

    async def fail_after_turn(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)
        raise RuntimeError("injected loss before Inbox association")

    with monkeypatch.context() as patch:
        patch.setattr(client.app.state.workspace_service, "create_turn", fail_after_turn)
        assert client.post(url).status_code == 500
    assert counts(client) == (1, 0, 0)
    assert client.post(url).status_code == 200
    assert counts(client) == (1, 1, 1)


def test_list_recovery_pagination_is_scoped_and_revocable(client: TestClient) -> None:
    first = provision(client)
    second = provision(client, "second")
    ids = []
    for index in range(3):
        receipt = client.post(first["url"], json={**MESSAGE, "vendor_event_id": str(index)})
        assert receipt.status_code == 202
        ids.append(receipt.json()["id"])
    page = client.get(first["url"], params={"limit": 2}).json()
    assert [row["id"] for row in page] == sorted(ids)[:2]
    remaining = client.get(first["url"], params={"after": page[-1]["id"]}).json()
    assert [row["id"] for row in remaining] == sorted(ids)[2:]
    assert client.get(second["url"]).json() == []
    assert client.get(first["url"], params={"limit": 101}).status_code == 422
    assert client.post(f"{ADMIN}/{first['id']}/revoke").status_code == 200
    assert client.get(first["url"]).status_code == 403


def test_multiturn_history_is_installation_and_subject_scoped(client: TestClient) -> None:
    first = provision(client)
    second = provision(client, "second")
    for installation, event in ((first, "a"), (first, "b"), (second, "c")):
        receipt = client.post(installation["url"], json={**MESSAGE, "vendor_event_id": event})
        assert receipt.status_code == 202
        result = client.post(f"{installation['url']}/{receipt.json()['id']}/process")
        assert result.status_code == 200, result.text

    async def read() -> list[tuple[UUID, int]]:
        async with client.app.state.database.sessions() as session:
            turns = list(await session.scalars(select(Turn).order_by(Turn.created_at)))
            assert all(ref["type"] != "im_delivery" for turn in turns for ref in turn.context_refs)
            return [(turn.thread_id, turn.ordinal) for turn in turns]

    turns = asyncio.run(read())
    assert turns[0][0] == turns[1][0]
    assert turns[0][0] != turns[2][0]
    assert [row[1] for row in turns] == [1, 2, 1]


def test_precreated_labels_cannot_capture_im_context(client: TestClient) -> None:
    installation = provision(client)
    fake_workspace = client.post(
        "/api/v1/workspaces",
        json={
            "name": f"IM:{installation['id']}:{installation['auth']['principal_id']}",
            "visibility": "PRIVATE",
        },
    )
    assert fake_workspace.status_code == 201
    digest = hashlib.sha256(
        json.dumps(
            [
                MESSAGE["sender_id"],
                MESSAGE["conversation_type"],
                MESSAGE["conversation_id"],
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    fake_thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": fake_workspace.json()["id"], "title": f"im:{digest}"},
    )
    assert fake_thread.status_code == 201
    receipt = client.post(installation["url"], json=MESSAGE).json()
    result = client.post(f"{installation['url']}/{receipt['id']}/process")
    assert result.status_code == 200, result.text

    async def check() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            mapping = await session.scalar(select(ImConversationBinding))
            assert mapping is not None
            assert str(mapping.workspace_id) != fake_workspace.json()["id"]
            assert str(mapping.thread_id) != fake_thread.json()["id"]
            workspace = await session.get(Workspace, mapping.workspace_id)
            thread = await session.get(Thread, mapping.thread_id)
            assert workspace is not None and thread is not None
            workspace.name = "renamed workspace"
            thread.title = "renamed thread"

    asyncio.run(check())
    next_receipt = client.post(installation["url"], json={**MESSAGE, "vendor_event_id": "second"})
    assert (
        client.post(f"{installation['url']}/{next_receipt.json()['id']}/process").status_code == 200
    )

    async def same_thread() -> None:
        async with client.app.state.database.sessions() as session:
            turns = list(await session.scalars(select(Turn)))
            assert len(turns) == 2 and turns[0].thread_id == turns[1].thread_id

    asyncio.run(same_thread())


@pytest.mark.parametrize("sharing", ["organization", "member"])
def test_shared_mapped_workspace_fails_closed(client: TestClient, sharing: str) -> None:
    installation = provision(client)
    receipt = client.post(installation["url"], json=MESSAGE).json()
    assert client.post(f"{installation['url']}/{receipt['id']}/process").status_code == 200
    other = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "member",
            "email": "member@obsion.dev",
            "display_name": "Member",
        },
    ).json()

    async def share() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            mapping = await session.scalar(select(ImConversationBinding))
            assert mapping is not None
            workspace = await session.get(Workspace, mapping.workspace_id)
            assert workspace is not None
            if sharing == "organization":
                workspace.visibility = Visibility.ORGANIZATION
            else:
                session.add(
                    WorkspaceMember(
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.id,
                        user_id=UUID(other["id"]),
                        permissions=["read"],
                        can_write=False,
                        created_by=workspace.owner_id,
                        created_at=utc_now(),
                    )
                )

    asyncio.run(share())
    next_receipt = client.post(installation["url"], json={**MESSAGE, "vendor_event_id": "second"})
    response = client.post(f"{installation['url']}/{next_receipt.json()['id']}/process")
    assert response.status_code == 403, response.text
    assert counts(client) == (2, 1, 1)


@pytest.mark.parametrize("connector_type", ["feishu-docs", "wecom-docs", "dingtalk-http", "http"])
def test_installation_requires_matching_supported_connector(
    client: TestClient,
    connector_type: str,
) -> None:
    installation = provision(client)

    async def change_type() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            connector = await session.get(Connector, UUID(installation["connector_id"]))
            assert connector is not None
            connector.connector_type = connector_type

    asyncio.run(change_type())
    request = {
        key: installation[key]
        for key in (
            "provider",
            "external_corp_id",
            "external_app_id",
            "connector_id",
            "adapter_principal_id",
            "verification_source",
        )
    }
    request["external_app_id"] = "another-app"
    assert client.post(ADMIN, json=request).status_code == 404
    assert client.post(installation["url"], json=MESSAGE).status_code == 403
    assert counts(client) == (0, 0, 0)


def test_legacy_binding_never_authorizes_new_installation(client: TestClient) -> None:
    installation = provision(client)
    assert (
        client.post(
            f"{ADMIN}/{installation['id']}/bindings/{installation['binding_id']}/revoke"
        ).status_code
        == 200
    )
    legacy = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "dingtalk",
            "sender_id": "sender-1",
            "user_id": installation["auth"]["principal_id"],
        },
    )
    assert legacy.status_code == 201
    assert client.post(installation["url"], json=MESSAGE).status_code == 403
    assert counts(client) == (0, 0, 0)
