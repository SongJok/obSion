from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_knowledge_sync_worker import setup

from obsion.capabilities.dingtalk_managed import MANAGED_CONNECTOR_TYPE, MANAGED_READ_OPERATION
from obsion.capabilities.gateway import GatewayResult, GatewayStatus
from obsion.common.time import utc_now
from obsion.db.models import (
    AuditRecord,
    Connector,
    KnowledgeSyncSource,
    Policy,
    User,
)
from obsion.domain.enums import DecisionEffect

BASE = "/api/v1/knowledge/sources/dingtalk/managed"


def test_source_management_registers_controls_and_preserves_disabled_access(
    client: TestClient,
) -> None:
    async def prepare():
        principal, source, service = await setup(client)
        async with client.app.state.database.sessions() as session, session.begin():
            connector = await session.scalar(
                select(Connector).where(Connector.name == "managed_reader_test")
            )
            await service.accept_read(
                session,
                principal,
                source_id=source.id,
                correlation_id=uuid4(),
                result=GatewayResult(
                    status=GatewayStatus.COMPLETED,
                    connector_id=connector.id,
                    policy_decision_id=uuid4(),
                    output={
                        "operation": MANAGED_READ_OPERATION,
                        "adapter": MANAGED_CONNECTOR_TYPE,
                        "corp_id": source.corp_id,
                        "binding_id": str(source.principal_binding_id),
                        "reader_user_id": str(principal.id),
                        "node_id": "document_one",
                        "workspace_id": "workspace_one",
                        "title": "采购制度",
                        "revision": "4",
                        "parser_version": "fixture",
                        "raw_checksum_sha256": "a" * 64,
                        "observed_at": utc_now().isoformat(),
                        "complete": True,
                        "gaps": [],
                        "text": "采购需要财务复核。",
                    },
                ),
            )
            return str(source.id), str(connector.id), str(source.principal_binding_id)

    source_id, connector_id, binding_id = client.portal.call(prepare)
    offered = client.get(f"{BASE}/connections")
    assert offered.status_code == 200, offered.text
    assert len(offered.json()) == 1 and offered.json()[0]["connector_id"] == connector_id
    assert not any(
        word in offered.text for word in ("app_key", "credential", "configuration", "test-secret")
    )
    registered = client.post(BASE, json={"connector_id": connector_id, "binding_id": binding_id})
    assert registered.status_code == 201, registered.text
    assert registered.json()["id"] == source_id
    listing = client.get(BASE)
    assert listing.status_code == 200, listing.text
    view = listing.json()["items"][0]
    assert view["counts"]["available"] == 1 and view["counts"]["partial"] == 1
    items = client.get(f"{BASE}/{source_id}/items?limit=1").json()
    assert len(items["items"]) == 1 and items["next_cursor"]
    second = client.get(f"{BASE}/{source_id}/items?limit=1&cursor={items['next_cursor']}").json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert second["items"][0]["id"] != items["items"][0]["id"]
    paused = client.post(f"{BASE}/{source_id}/control", json={"operation": "pause"})
    assert paused.status_code == 200 and paused.json()["state"] == "PAUSED"
    assert paused.json()["counts"]["available"] == 0
    assert all(
        row["document_id"] is None
        for row in client.get(f"{BASE}/{source_id}/items").json()["items"]
    )
    assert client.post(f"{BASE}/{source_id}/control", json={"operation": "sync"}).status_code == 422
    resumed = client.post(f"{BASE}/{source_id}/control", json={"operation": "resume"})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == "QUEUED" and resumed.json()["counts"]["available"] == 0
    assert client.post(f"{BASE}/{source_id}/control", json={"operation": "sync"}).status_code == 200
    assert client.get(f"{BASE}?limit=0").status_code == 422
    assert client.get(f"{BASE}/{source_id}/items?cursor=opaque-invalid").status_code == 422
    assert (
        client.post(
            BASE,
            json={
                "connector_id": connector_id,
                "binding_id": binding_id,
                "text": "untrusted proof",
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize("denial", ["policy", "other_owner", "changed_connection"])
def test_management_denials_are_scoped_and_committed(client: TestClient, denial: str) -> None:
    async def prepare():
        principal, source, _ = await setup(client)
        async with client.app.state.database.sessions() as session, session.begin():
            current = await session.get(KnowledgeSyncSource, source.id)
            if denial == "policy":
                policies = (
                    await session.scalars(
                        select(Policy).where(Policy.name == "source worker explicit test allow")
                    )
                ).all()
                policies[0].effect = DecisionEffect.DENY
            elif denial == "other_owner":
                other = User(
                    organization_id=principal.organization_id,
                    external_id="source-other",
                    email="source-other@example.test",
                    display_name="Other owner",
                    active=True,
                )
                session.add(other)
                await session.flush()
                current.user_id = other.id
            else:
                connector = await session.scalar(
                    select(Connector).where(Connector.name == "managed_reader_test")
                )
                connector.configuration = {**connector.configuration, "operator_id": "changed"}
            return str(source.id)

    source_id = client.portal.call(prepare)
    response = client.post(f"{BASE}/{source_id}/control", json={"operation": "resume"})
    assert response.status_code == (404 if denial == "other_owner" else 403), response.text
    if denial == "other_owner":
        assert client.get(BASE).json()["items"] == []
        assert client.get(f"{BASE}/{source_id}/items").status_code == 404

    async def check():
        async with client.app.state.database.sessions() as session:
            audits = (
                await session.scalars(
                    select(AuditRecord).where(
                        AuditRecord.resource_type == "knowledge_source",
                        AuditRecord.outcome == "DENIED",
                    )
                )
            ).all()
            assert audits and all(row.policy_decision_id is not None for row in audits)

    client.portal.call(check)


def test_scan_completion_does_not_mean_content_or_permissions_are_current(
    client: TestClient,
) -> None:
    async def prepare():
        _, source, _ = await setup(client)
        async with client.app.state.database.sessions() as session, session.begin():
            current = await session.get(KnowledgeSyncSource, source.id)
            current.last_success_at = utc_now()
            current.next_poll_at = utc_now() + timedelta(seconds=60)

    client.portal.call(prepare)
    result = client.get(BASE)
    assert result.status_code == 200, result.text
    view = result.json()["items"][0]
    assert view["state"] == "ATTENTION" and view["counts"]["partial"] == 1
    assert view["counts"]["available"] == 0 and view["last_scan_completed_at"] is not None


def test_source_control_uses_authoritative_organization_in_policy(client: TestClient) -> None:
    async def prepare():
        _, source, _ = await setup(client)
        async with client.app.state.database.sessions() as session, session.begin():
            policy = await session.scalar(
                select(Policy).where(Policy.name == "source worker explicit test allow")
            )
            policy.conditions = {
                "actions": ["knowledge.write"],
                "resource": {"corp_id": "corp_test"},
            }
            return str(source.id)

    source_id = client.portal.call(prepare)
    response = client.post(f"{BASE}/{source_id}/control", json={"operation": "sync"})
    assert response.status_code == 200, response.text
    assert response.json()["corp_id"] == "corp_test"
