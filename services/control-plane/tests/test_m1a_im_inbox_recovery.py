"""Durable admission and stale-worker recovery contracts (local database)."""

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from obsion.application.im_inbox_worker import ImInboxWorker
from obsion.common.time import utc_now
from obsion.db.models import ImInboxEvent
from obsion.domain.enums import ImInboxStatus


@pytest.fixture
def admitted(client: TestClient):
    client.portal.call(client.app.state.im_inbox_worker.stop)
    user = client.get("/api/v1/auth/session").json()["principal_id"]
    installation = client.post(
        "/api/v1/admin/im-installations",
        json={
            "channel": "dingtalk",
            "installation_id": "installation",
            "corp_id": "corp",
            "app_key": "app",
        },
    )
    assert installation.status_code == 201, installation.text
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "dingtalk",
            "sender_id": "sender",
            "user_id": user,
            "installation_id": installation.json()["id"],
        },
    )
    assert binding.status_code == 201, binding.text
    payload = dict(
        channel="dingtalk",
        installation_id="installation",
        corp_id="corp",
        app_key="app",
        vendor_event_id="event",
        sender_id="sender",
        conversation_id="conversation",
        text="你好",
        is_group=True,
    )
    response = client.post("/api/v1/experience/im/trusted-events", json=payload)
    assert response.status_code == 202, response.text
    return installation.json()["id"], payload, response.json()


def test_exact_replay_and_changed_payload(client: TestClient, admitted):
    _, payload, accepted = admitted
    replay = client.post("/api/v1/experience/im/trusted-events", json=payload)
    assert replay.status_code == 202, replay.text
    assert replay.json()["inbox_event_id"] == accepted["inbox_event_id"]
    assert accepted["run_id"] is None
    mismatch = client.post(
        "/api/v1/experience/im/trusted-events", json={**payload, "text": "different"}
    )
    assert mismatch.status_code == 409, mismatch.text


@pytest.mark.parametrize("operation", ["_dispatch", "_release", "_reject"])
def test_old_attempt_cannot_mutate_reclaimed_event(client: TestClient, admitted, operation):
    _, _, accepted = admitted
    worker = client.app.state.im_inbox_worker

    async def exercise():
        old = await worker._claim()
        assert old is not None
        async with worker.database.sessions() as session, session.begin():
            event = await session.get(ImInboxEvent, old.event_id)
            event.lease_expires_at = utc_now() - timedelta(seconds=1)
        # Same worker identity, different generation: owner equality is insufficient.
        new = await worker._claim()
        assert new is not None and new.attempt > old.attempt
        args = (old, "unknown_im_sender") if operation == "_reject" else (old,)
        await getattr(worker, operation)(*args)
        async with worker.database.sessions() as session:
            event = await session.get(ImInboxEvent, UUID(accepted["inbox_event_id"]))
            assert event.status == ImInboxStatus.PROCESSING
            assert event.attempt_count == new.attempt
            assert event.run_id is None

    client.portal.call(exercise)


def test_revoked_installation_rejected_before_dispatch(client: TestClient, admitted):
    installation, _, accepted = admitted
    response = client.post(f"/api/v1/admin/im-installations/{installation}/revoke")
    assert response.status_code == 200, response.text
    worker: ImInboxWorker = client.app.state.im_inbox_worker

    async def exercise():
        claim = await worker._claim()
        assert claim is not None
        await worker._dispatch(claim)
        async with worker.database.sessions() as session:
            event = await session.get(ImInboxEvent, UUID(accepted["inbox_event_id"]))
            assert event.status == ImInboxStatus.REJECTED
            assert event.run_id is None
            assert event.rejected_code == "unknown_im_sender"

    client.portal.call(exercise)


def test_another_installation_cannot_reuse_sender_binding(client: TestClient, admitted):
    _, payload, _ = admitted
    response = client.post(
        "/api/v1/admin/im-installations",
        json={
            "channel": "dingtalk",
            "installation_id": "other-app",
            "corp_id": "other-corp",
            "app_key": "other-key",
        },
    )
    assert response.status_code == 201
    denied = client.post(
        "/api/v1/experience/im/trusted-events",
        json={
            **payload,
            "installation_id": "other-app",
            "corp_id": "other-corp",
            "app_key": "other-key",
        },
    )
    assert denied.status_code == 403, denied.text


def test_reassigned_sender_does_not_reassign_queued_task(client: TestClient, admitted):
    installation, _, accepted = admitted
    user = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "replacement",
            "email": "replacement@obsion.dev",
            "display_name": "Replacement",
            "attributes": {},
        },
    )
    assert user.status_code == 201
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "dingtalk",
            "installation_id": installation,
            "sender_id": "sender",
            "user_id": user.json()["id"],
        },
    )
    assert binding.status_code == 201
    worker = client.app.state.im_inbox_worker

    async def exercise():
        claim = await worker._claim()
        assert claim is not None
        await worker._dispatch(claim)
        async with worker.database.sessions() as session:
            event = await session.get(ImInboxEvent, UUID(accepted["inbox_event_id"]))
            assert event.status == ImInboxStatus.REJECTED
            assert event.run_id is None

    client.portal.call(exercise)


def test_credentials_are_redacted_before_inbox_persistence(client: TestClient, admitted):
    _, payload, _ = admitted
    response = client.post(
        "/api/v1/experience/im/trusted-events",
        json={
            **payload,
            "vendor_event_id": "sensitive",
            "text": "token=fixture-private-value",
        },
    )
    assert response.status_code == 202
    replay = client.post(
        "/api/v1/experience/im/trusted-events",
        json={
            **payload,
            "vendor_event_id": "sensitive",
            "text": "token=another-fixture-private-value",
        },
    )
    assert replay.status_code == 202
    assert replay.json()["duplicate"] is True
    assert replay.json()["inbox_event_id"] == response.json()["inbox_event_id"]
    worker = client.app.state.im_inbox_worker

    async def inspect():
        async with worker.database.sessions() as session:
            event = await session.get(ImInboxEvent, UUID(response.json()["inbox_event_id"]))
            assert event.text == "token=[REDACTED]"

    client.portal.call(inspect)


def test_inbox_status_is_content_free_and_principal_scoped(client: TestClient, admitted):
    from uuid import uuid4

    from obsion.security.auth import get_principal
    from obsion.security.identity import Principal

    _, _, accepted = admitted
    path = f"/api/v1/experience/im/trusted-events/{accepted['inbox_event_id']}"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["safe_status"] == "received"
    assert set(response.json()) == {
        "inbox_event_id",
        "status",
        "duplicate",
        "run_id",
        "safe_status",
        "intent",
    }
    auth = client.get("/api/v1/auth/session").json()
    outsider = Principal(
        id=uuid4(),
        organization_id=UUID(auth["organization_id"]),
        external_id="outsider",
        display_name="Outsider",
    )
    client.app.dependency_overrides[get_principal] = lambda: outsider
    try:
        assert client.get(path).status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal)


def test_repeated_dispatch_failures_are_bounded_and_audited(
    client: TestClient, admitted, monkeypatch
):
    _, _, accepted = admitted
    worker = client.app.state.im_inbox_worker

    async def unavailable(*args):
        raise RuntimeError("fixture unavailable")

    monkeypatch.setattr(worker.service, "dispatch_inbox_event", unavailable)

    async def exercise():
        for _ in range(worker.settings.im_inbox_max_attempts):
            claim = await worker._claim()
            assert claim is not None
            await worker._dispatch(claim)
        assert await worker._claim() is None
        async with worker.database.sessions() as session:
            event = await session.get(ImInboxEvent, UUID(accepted["inbox_event_id"]))
            assert event.status == ImInboxStatus.REJECTED
            assert event.rejected_code == "internal_error"
            assert event.attempt_count == worker.settings.im_inbox_max_attempts

    client.portal.call(exercise)
    audit = client.get("/api/v1/admin/audit?limit=100").json()
    assert any(
        row["action"] == "identity.im.inbox.reject"
        and row["resource_id"] == accepted["inbox_event_id"]
        for row in audit
    )


def test_worker_loop_survives_recovery_transaction_failure(
    client: TestClient, admitted, monkeypatch
):
    worker = client.app.state.im_inbox_worker
    calls = []
    original_claim = worker._claim

    async def claim_then_stop():
        if calls:
            worker._stop.set()
            return None
        return await original_claim()

    async def unavailable(claim):
        calls.append(claim)
        raise RuntimeError("fixture recovery transaction unavailable")

    monkeypatch.setattr(worker, "_claim", claim_then_stop)
    monkeypatch.setattr(worker, "_dispatch", unavailable)

    async def exercise():
        worker._stop.clear()
        await worker._loop()
        assert len(calls) == 1

    client.portal.call(exercise)


def _dispatch_completed_run(client, accepted):
    import time

    worker = client.app.state.im_inbox_worker

    async def dispatch():
        claim = await worker._claim()
        assert claim is not None
        await worker._dispatch(claim)

    client.portal.call(dispatch)
    status = client.get(f"/api/v1/experience/im/trusted-events/{accepted['inbox_event_id']}")
    assert status.status_code == 200
    run_id = status.json()["run_id"]
    assert run_id is not None
    for _ in range(100):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            assert run["status"] == "COMPLETED"
            return run_id
        time.sleep(0.05)
    raise AssertionError("Run did not finish")


def test_group_delivery_uses_persisted_audience_not_context_hint(client: TestClient, admitted):
    from obsion.db.models import Run, Turn

    _, _, accepted = admitted
    run_id = _dispatch_completed_run(client, accepted)
    run_view = client.get(f"/api/v1/runs/{run_id}")
    assert run_view.status_code == 200
    assert run_view.json()["intent"]["admission_intent"] == "QUERY"
    worker = client.app.state.im_inbox_worker

    async def tamper_hint():
        async with worker.database.sessions() as session, session.begin():
            run = await session.get(Run, UUID(run_id))
            turn = await session.get(Turn, run.turn_id)
            turn.context_refs = [{**item, "group_status_only": False} for item in turn.context_refs]

    client.portal.call(tamper_hint)
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert prepared.status_code == 200, prepared.text
    assert run_id in prepared.json()["text"]
    assert "你好" not in prepared.json()["text"]


def test_installation_revoked_after_run_cannot_prepare_delivery(client: TestClient, admitted):
    installation, _, accepted = admitted
    run_id = _dispatch_completed_run(client, accepted)
    assert client.post(f"/api/v1/admin/im-installations/{installation}/revoke").status_code == 200
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert prepared.status_code == 403, prepared.text


def test_event_id_deduplication_is_scoped_to_explicit_installation(client: TestClient, admitted):
    _, payload, accepted = admitted
    user_id = client.get("/api/v1/auth/session").json()["principal_id"]
    installation = client.post(
        "/api/v1/admin/im-installations",
        json={
            "channel": "dingtalk",
            "installation_id": "second-install",
            "corp_id": "second-corp",
            "app_key": "second-app",
        },
    )
    assert installation.status_code == 201
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "dingtalk",
            "installation_id": installation.json()["id"],
            "sender_id": "sender",
            "user_id": user_id,
        },
    )
    assert binding.status_code == 201, binding.text
    response = client.post(
        "/api/v1/experience/im/trusted-events",
        json={
            **payload,
            "installation_id": "second-install",
            "corp_id": "second-corp",
            "app_key": "second-app",
        },
    )
    assert response.status_code == 202
    assert response.json()["inbox_event_id"] != accepted["inbox_event_id"]
    assert response.json()["duplicate"] is False


def test_cross_organization_delegate_cannot_use_installation(client: TestClient, admitted):
    from uuid import uuid4

    from obsion.security.auth import get_principal
    from obsion.security.identity import Principal

    _, payload, _ = admitted
    outsider = Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="other-org",
        display_name="Other",
        permissions=frozenset({"im.delegate"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: outsider
    try:
        assert client.post("/api/v1/experience/im/trusted-events", json=payload).status_code == 403
    finally:
        client.app.dependency_overrides.pop(get_principal)


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("查询支付服务状态", "QUERY"),
        ("总结这份事故报告", "SUMMARY"),
        ("分析发布后延迟上升的原因", "ANALYSIS"),
        ("创建一份排障报告", "CREATE"),
    ],
)
def test_admission_pins_one_of_four_narrowing_intents(
    client: TestClient, admitted, text: str, intent: str
) -> None:
    _, payload, _ = admitted
    response = client.post(
        "/api/v1/experience/im/trusted-events",
        json={**payload, "vendor_event_id": f"intent-{intent}", "text": text},
    )
    assert response.status_code == 202, response.text
    assert response.json()["intent"] == intent

    audit = client.get("/api/v1/admin/audit?limit=100").json()
    acceptance = next(
        row
        for row in audit
        if row["resource_id"] == response.json()["inbox_event_id"]
        and row["action"] == "identity.im.inbox.accept"
    )
    assert acceptance["metadata"]["intent"] == intent
    assert acceptance["metadata"]["intent_reason"]
