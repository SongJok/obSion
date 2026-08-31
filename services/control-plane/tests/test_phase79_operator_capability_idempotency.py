from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select

from obsion.api.dependencies import get_capability_gateway
from obsion.capabilities.feishu_docs import FeishuDocument, FeishuWikiSpace
from obsion.common.time import utc_now
from obsion.db.models import OperatorCapabilityInvocation
from obsion.domain.enums import OperatorInvocationStatus


def _request(client: TestClient, request_id: UUID, document_id: str) -> Response:
    return client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        headers={"X-Request-ID": str(request_id)},
        json={"document_id": document_id, "acl": {"organization": True}},
    )


def _install_fetch(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
) -> None:
    async def fetch(**kwargs: object) -> FeishuDocument:
        document_id = str(kwargs["document_id"])
        calls.append(document_id)
        return FeishuDocument(
            document_id=document_id,
            title=f"Idempotent {document_id}",
            content="A no-Run operator write executes exactly once.",
            revision_id="79",
            obj_type="docx",
            wiki_token=None,
        )

    monkeypatch.setattr("obsion.knowledge.feishu.fetch_authorized_feishu_document", fetch)


def test_exact_retry_replays_terminal_result_without_connector_execution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()

    first = _request(client, request_id, "doxcnPhase79Replay")
    second = _request(client, request_id, "doxcnPhase79Replay")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json() == first.json()
    assert calls == ["doxcnPhase79Replay"]

    audit = client.get("/api/v1/admin/audit?limit=100").json()
    outcomes = [
        item["outcome"]
        for item in audit
        if item["correlation_id"] == str(request_id) and item["action"] == "knowledge.write"
    ]
    assert outcomes == ["REPLAYED", "SUCCESS"]


def test_server_generated_request_id_can_be_reused_from_response_header(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    payload = {
        "document_id": "doxcnPhase79ServerRequest",
        "acl": {"organization": True},
    }
    first = client.post("/api/v1/knowledge/sources/feishu/documents", json=payload)
    request_id = first.headers["X-Request-ID"]
    replay = client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        headers={"X-Request-ID": request_id},
        json=payload,
    )
    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert calls == ["doxcnPhase79ServerRequest"]


def test_non_uuid_request_id_fails_closed_instead_of_using_an_unreplayable_key(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)

    response = client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        headers={"X-Request-ID": "phase79-not-a-uuid"},
        json={
            "document_id": "doxcnPhase79InvalidRequest",
            "acl": {"organization": True},
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "capability_input_invalid"
    assert response.headers["X-Request-ID"] == "phase79-not-a-uuid"
    assert calls == []


def test_exact_retry_does_not_consume_rate_or_require_secret_again(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OneShotRateLimiter:
        def __init__(self) -> None:
            self.calls = 0

        async def allow(self, key: str, limit: int | None = None) -> bool:
            del key, limit
            self.calls += 1
            return self.calls == 1

    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()
    override = client.app.dependency_overrides.get(get_capability_gateway)
    gateway = override() if override is not None else client.app.state.capability_gateway
    original = gateway.rate_limiter
    limiter = OneShotRateLimiter()
    gateway.rate_limiter = limiter
    try:
        first = _request(client, request_id, "doxcnPhase79RateReplay")
        monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
        replay = _request(client, request_id, "doxcnPhase79RateReplay")
    finally:
        gateway.rate_limiter = original

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert limiter.calls == 1
    assert calls == ["doxcnPhase79RateReplay"]


def test_exact_retry_reauthorizes_before_replaying_terminal_result(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()
    first = _request(client, request_id, "doxcnPhase79Reauthorize")
    assert first.status_code == 201, first.text

    policy = client.post(
        "/api/v1/admin/policies",
        json={
            "name": "phase79-replay-reauthorization",
            "priority": 9999,
            "effect": "ASK",
            "conditions": {
                "actions": ["knowledge.write"],
                "resource": {"source": "feishu"},
                "context": {"invocation_mode": "operator"},
            },
            "obligations": [],
            "reason": "Replays must satisfy current Policy",
        },
    )
    assert policy.status_code == 201, policy.text

    replay = _request(client, request_id, "doxcnPhase79Reauthorize")
    assert replay.status_code == 403, replay.text
    assert replay.json()["code"] == "capability_denied"
    assert calls == ["doxcnPhase79Reauthorize"]


def test_request_id_reuse_with_different_input_is_conflict_before_secret(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()
    first = _request(client, request_id, "doxcnPhase79Original")
    assert first.status_code == 201, first.text

    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    conflict = _request(client, request_id, "doxcnPhase79Different")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "idempotency_key_reused"
    assert calls == ["doxcnPhase79Original"]


def test_failed_result_is_replayed_without_later_secret_changing_outcome(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()
    first = _request(client, request_id, "doxcnPhase79Failure")
    assert first.status_code == 422, first.text
    assert first.json()["code"] == "credential_unavailable"

    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "now-available")
    replay = _request(client, request_id, "doxcnPhase79Failure")
    assert replay.status_code == 422, replay.text
    assert replay.json()["code"] == "credential_unavailable"
    assert calls == []


def test_side_effect_free_browse_is_audited_but_not_put_in_write_ledger(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls = 0

    async def spaces(*args: object, **kwargs: object) -> list[FeishuWikiSpace]:
        nonlocal calls
        del args, kwargs
        calls += 1
        return [FeishuWikiSpace("7365887900", "Phase 79", "read-only")]

    monkeypatch.setattr("obsion.knowledge.feishu.list_feishu_spaces", spaces)
    request_id = uuid4()
    for _ in range(2):
        response = client.get(
            "/api/v1/knowledge/sources/feishu/spaces",
            headers={"X-Request-ID": str(request_id)},
        )
        assert response.status_code == 200, response.text
    assert calls == 2
    ledger = client.get("/api/v1/admin/operator-invocations?limit=100")
    assert ledger.status_code == 200, ledger.text
    assert all(item["request_id"] != str(request_id) for item in ledger.json())


def test_unexpired_in_progress_attempt_is_not_executed_twice(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()
    first = _request(client, request_id, "doxcnPhase79InProgress")
    assert first.status_code == 201, first.text

    async def restore_in_progress() -> None:
        database = client.app.state.database
        async with database.sessions() as session, session.begin():
            record = await session.scalar(
                select(OperatorCapabilityInvocation).where(
                    OperatorCapabilityInvocation.request_id == request_id
                )
            )
            assert record is not None
            record.status = OperatorInvocationStatus.IN_PROGRESS
            record.result = None
            record.error_code = None
            record.error_message = None
            record.completed_at = None
            record.lease_expires_at = utc_now() + timedelta(minutes=1)

    asyncio.run(restore_in_progress())
    retry = _request(client, request_id, "doxcnPhase79InProgress")
    assert retry.status_code == 409, retry.text
    assert retry.json()["code"] == "idempotency_request_in_progress"
    assert calls == ["doxcnPhase79InProgress"]


def test_expired_in_progress_attempt_becomes_unknown_and_never_auto_retries(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase79-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase79-secret")
    calls: list[str] = []
    _install_fetch(monkeypatch, calls)
    request_id = uuid4()
    first = _request(client, request_id, "doxcnPhase79Unknown")
    assert first.status_code == 201, first.text

    async def expire_attempt() -> None:
        database = client.app.state.database
        async with database.sessions() as session, session.begin():
            record = await session.scalar(
                select(OperatorCapabilityInvocation).where(
                    OperatorCapabilityInvocation.request_id == request_id
                )
            )
            assert record is not None
            record.status = OperatorInvocationStatus.IN_PROGRESS
            record.result = None
            record.error_code = None
            record.error_message = None
            record.completed_at = None
            record.lease_expires_at = utc_now() - timedelta(seconds=1)

    asyncio.run(expire_attempt())
    retry = _request(client, request_id, "doxcnPhase79Unknown")
    assert retry.status_code == 409, retry.text
    assert retry.json()["code"] == "operator_invocation_outcome_unknown"
    assert calls == ["doxcnPhase79Unknown"]

    async def status() -> OperatorInvocationStatus:
        database = client.app.state.database
        async with database.sessions() as session:
            value = await session.scalar(
                select(OperatorCapabilityInvocation.status).where(
                    OperatorCapabilityInvocation.request_id == request_id
                )
            )
            assert value is not None
            return value

    assert asyncio.run(status()) == OperatorInvocationStatus.UNKNOWN

    listing = client.get("/api/v1/admin/operator-invocations?status=UNKNOWN")
    assert listing.status_code == 200, listing.text
    item = next(row for row in listing.json() if row["request_id"] == str(request_id))
    assert item["status"] == "UNKNOWN"
    assert item["reconciliation_required"] is True
    assert item["error_code"] == "operator_invocation_outcome_unknown"
    assert "result" not in item
    assert "input" not in item
