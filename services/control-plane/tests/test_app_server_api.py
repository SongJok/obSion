import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from obsion.app_server.protocol import PROTOCOL_VERSION, WEBSOCKET_SUBPROTOCOL
from obsion.config import Settings
from obsion.main import create_app


def _initialize(client: TestClient):  # type: ignore[no-untyped-def]
    websocket = client.websocket_connect(
        "/api/v1/app-server",
        subprotocols=[WEBSOCKET_SUBPROTOCOL],
        headers={"origin": "http://testserver"},
    )
    session = websocket.__enter__()
    assert session.accepted_subprotocol == WEBSOCKET_SUBPROTOCOL
    ready = session.receive_json()
    assert ready["method"] == "server.ready"
    session.send_json(
        {
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "server.initialize",
            "params": {
                "protocol_version": PROTOCOL_VERSION,
                "client_name": "pytest",
                "client_version": "1.0.0",
                "bearer_token": client.app.state.settings.dev_bearer_token.get_secret_value(),
            },
        }
    )
    initialized = session.receive_json()
    assert initialized["id"] == "initialize"
    assert initialized["result"]["protocol_version"] == PROTOCOL_VERSION
    return websocket, session


def _rpc(session, request_id: str, method: str, params: dict) -> dict:  # type: ignore[no-untyped-def]
    session.send_json(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
    )
    return session.receive_json()


def _workspace(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": "App Server", "description": "Unified protocol acceptance"},
        headers={
            "Authorization": (
                f"Bearer {client.app.state.settings.dev_bearer_token.get_secret_value()}"
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_app_server_unifies_lifecycle_idempotency_and_resumable_streaming(
    client: TestClient,
) -> None:
    workspace = _workspace(client)
    context, session = _initialize(client)
    try:
        listed = _rpc(session, "list-workspaces", "workspace.list", {})
        assert [item["id"] for item in listed["result"]] == [workspace["id"]]

        create_params = {
            "client_request_id": "thread-create-1",
            "workspace_id": workspace["id"],
            "title": "Payment investigation",
        }
        created = _rpc(session, "create-thread", "thread.create", create_params)
        thread = created["result"]
        repeated = _rpc(session, "create-thread-retry", "thread.create", create_params)
        assert repeated["result"] == thread
        threads = _rpc(
            session,
            "list-threads",
            "thread.list",
            {"workspace_id": workspace["id"]},
        )
        assert [item["id"] for item in threads["result"]] == [thread["id"]]

        conflicting = _rpc(
            session,
            "create-thread-conflict",
            "thread.create",
            {**create_params, "title": "A different operation"},
        )
        assert conflicting["error"]["data"]["code"] == "idempotency_key_reused"

        failed_params = {
            "client_request_id": "thread-create-not-found",
            "workspace_id": "00000000-0000-7000-8000-000000000099",
            "title": "Cannot be created",
        }
        failed = _rpc(session, "failed-create", "thread.create", failed_params)
        failed_retry = _rpc(
            session,
            "failed-create-retry",
            "thread.create",
            failed_params,
        )
        assert failed_retry["error"] == failed["error"]
        assert failed["error"]["data"]["code"] == "resource_not_found"

        created_turn = _rpc(
            session,
            "create-turn",
            "turn.create",
            {
                "client_request_id": "turn-create-1",
                "thread_id": thread["id"],
                "input": "Summarize the governed evidence.",
            },
        )["result"]
        run_id = created_turn["run"]["id"]
        run = created_turn["run"]
        for _ in range(100):
            run = client.get(f"/api/v1/runs/{run_id}").json()
            if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.05)
        assert run["status"] == "COMPLETED", run

        subscribed = _rpc(
            session,
            "subscribe",
            "run.subscribe",
            {"run_id": run_id, "after_sequence": 0},
        )["result"]
        assert subscribed["after_sequence"] == 0
        delivered = []
        while True:
            message = session.receive_json()
            if message["method"] == "run.subscription.completed":
                completed = message["params"]
                break
            delivered.append(message)
        assert delivered
        assert all(
            item["params"]["subscription_id"] == subscribed["subscription_id"] for item in delivered
        )
        run_sequences = [item["params"]["event"]["run_sequence"] for item in delivered]
        assert run_sequences == list(range(1, len(run_sequences) + 1))
        assert completed["after_sequence"] == run_sequences[-1]
        assert {"run.started", "run.completed"}.issubset({item["method"] for item in delivered})
        rest_events = client.get(f"/api/v1/runs/{run_id}/events").json()
        assert [item["method"] for item in delivered] == [event["name"] for event in rest_events]
        assert [item["params"]["event"] for item in delivered] == rest_events
        assert [item["run_sequence"] for item in rest_events] == run_sequences
        midpoint = run_sequences[len(run_sequences) // 2]
        resumed_rest = client.get(f"/api/v1/runs/{run_id}/events?after={midpoint}").json()
        assert all(item["run_sequence"] > midpoint for item in resumed_rest)

        resumed = _rpc(
            session,
            "resume-subscription",
            "run.subscribe",
            {"run_id": run_id, "after_sequence": run_sequences[-1] - 1},
        )["result"]
        last_event = session.receive_json()
        assert last_event["params"]["subscription_id"] == resumed["subscription_id"]
        assert last_event["params"]["event"]["run_sequence"] == run_sequences[-1]
        assert session.receive_json()["method"] == "run.subscription.completed"

        forked = _rpc(
            session,
            "fork-thread",
            "thread.fork",
            {
                "client_request_id": "thread-fork-1",
                "thread_id": thread["id"],
                "from_turn_id": created_turn["turn"]["id"],
                "title": "Payment investigation · alternative",
            },
        )["result"]
        assert forked["parent_thread_id"] == thread["id"]
        archived_threads = _rpc(
            session,
            "list-archived-threads",
            "thread.list",
            {"workspace_id": workspace["id"], "include_archived": True},
        )["result"]
        source = next(item for item in archived_threads if item["id"] == thread["id"])
        assert source["status"] == "ARCHIVED"

        rejected_source_turn = _rpc(
            session,
            "source-turn-after-fork",
            "turn.create",
            {
                "client_request_id": "source-turn-after-fork-1",
                "thread_id": thread["id"],
                "input": "The source branch is read-only after a fork.",
            },
        )
        assert rejected_source_turn["error"]["data"]["code"] == "thread_archived"
        restored = _rpc(
            session,
            "resume-source-thread",
            "thread.resume",
            {
                "client_request_id": "thread-resume-after-fork-1",
                "thread_id": thread["id"],
            },
        )["result"]
        assert restored["status"] == "ACTIVE"
    finally:
        context.__exit__(None, None, None)


def test_app_server_rejects_invalid_protocol_and_never_executes_notifications(
    client: TestClient,
) -> None:
    workspace = _workspace(client)
    context, session = _initialize(client)
    try:
        session.send_text("[]")
        assert session.receive_json()["error"]["code"] == -32600

        unknown = _rpc(session, "unknown", "runtime.unknown", {})
        assert unknown["error"]["code"] == -32601

        invalid = _rpc(session, "invalid", "thread.list", {"workspace_id": "invalid"})
        assert invalid["error"]["code"] == -32602

        session.send_json(
            {
                "jsonrpc": "2.0",
                "method": "thread.create",
                "params": {
                    "client_request_id": "notification-mutation",
                    "workspace_id": workspace["id"],
                    "title": "Must not exist",
                },
            }
        )
        warning = session.receive_json()
        assert warning["method"] == "server.warning"
        assert warning["params"]["code"] == "client_notification_ignored"
        assert client.get(f"/api/v1/workspaces/{workspace['id']}/threads").json() == []
    finally:
        context.__exit__(None, None, None)


def test_app_server_reconnect_resumes_from_a_durable_run_cursor(client: TestClient) -> None:
    workspace = _workspace(client)
    thread_response = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Reconnect acceptance"},
    )
    assert thread_response.status_code == 201, thread_response.text
    created = client.post(
        f"/api/v1/threads/{thread_response.json()['id']}/turns",
        json={"input": "Produce an event history that can be resumed."},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run"]["id"]
    for _ in range(100):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert run["status"] == "COMPLETED", run
    persisted = client.get(f"/api/v1/runs/{run_id}/events").json()
    assert len(persisted) > 2

    first_context, first_session = _initialize(client)
    try:
        subscription = _rpc(
            first_session,
            "initial-subscription",
            "run.subscribe",
            {"run_id": run_id, "after_sequence": 0},
        )["result"]
        first_event = first_session.receive_json()
        assert first_event["params"]["subscription_id"] == subscription["subscription_id"]
        cursor = first_event["params"]["event"]["run_sequence"]
    finally:
        first_context.__exit__(None, None, None)

    second_context, second_session = _initialize(client)
    try:
        resumed = _rpc(
            second_session,
            "resumed-subscription",
            "run.subscribe",
            {"run_id": run_id, "after_sequence": cursor},
        )["result"]
        resumed_events = []
        while True:
            message = second_session.receive_json()
            if message["method"] == "run.subscription.completed":
                completed = message["params"]
                break
            assert message["params"]["subscription_id"] == resumed["subscription_id"]
            resumed_events.append(message["params"]["event"])
    finally:
        second_context.__exit__(None, None, None)

    expected = [item for item in persisted if item["run_sequence"] > cursor]
    assert resumed_events == expected
    assert completed["after_sequence"] == persisted[-1]["run_sequence"]


def test_app_server_enforces_origin_subprotocol_and_protocol_version(
    client: TestClient,
) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as missing_protocol,
        client.websocket_connect("/api/v1/app-server", headers={"origin": "http://testserver"}),
    ):
        pass
    assert missing_protocol.value.code == 1008

    with (
        pytest.raises(WebSocketDisconnect) as denied_origin,
        client.websocket_connect(
            "/api/v1/app-server",
            subprotocols=[WEBSOCKET_SUBPROTOCOL],
            headers={"origin": "https://attacker.invalid"},
        ),
    ):
        pass
    assert denied_origin.value.code == 1008

    with client.websocket_connect(
        "/api/v1/app-server",
        subprotocols=[WEBSOCKET_SUBPROTOCOL],
        headers={"origin": "http://testserver"},
    ) as session:
        assert session.receive_json()["method"] == "server.ready"
        session.send_json(
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "server.initialize",
                "params": {
                    "protocol_version": "incompatible",
                    "client_name": "pytest",
                    "client_version": "1",
                },
            }
        )
        assert session.receive_json()["error"]["data"]["supported"] == [PROTOCOL_VERSION]
        with pytest.raises(WebSocketDisconnect) as closed:
            session.receive_json()
        assert closed.value.code == 1008


def test_app_server_principal_is_tenant_scoped(app_settings: Settings) -> None:
    with TestClient(
        create_app(app_settings),
        headers={"Authorization": f"Bearer {app_settings.dev_bearer_token.get_secret_value()}"},
    ) as owner_client:
        workspace = _workspace(owner_client)
        thread = owner_client.post(
            "/api/v1/threads",
            json={"workspace_id": workspace["id"], "title": "Tenant secret"},
        ).json()

    other_settings = app_settings.model_copy(
        update={
            "dev_organization_id": UUID("00000000-0000-7000-8000-000000000091"),
            "dev_user_id": UUID("00000000-0000-7000-8000-000000000092"),
        }
    )
    with TestClient(
        create_app(other_settings),
        headers={"Authorization": f"Bearer {other_settings.dev_bearer_token.get_secret_value()}"},
    ) as other_client:
        context, session = _initialize(other_client)
        try:
            assert _rpc(session, "list", "workspace.list", {})["result"] == []
            denied = _rpc(
                session,
                "turns",
                "thread.turns",
                {"thread_id": thread["id"]},
            )
            assert denied["error"]["data"]["code"] == "resource_not_found"
        finally:
            context.__exit__(None, None, None)
