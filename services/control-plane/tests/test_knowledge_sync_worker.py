from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_dingtalk_managed_reader import Native, Runner, install_fixture

from obsion.capabilities.dingtalk_managed import (
    MANAGED_DISCOVER_OPERATION,
    MANAGED_READ_OPERATION,
    DingTalkManagedSdkExecutor,
)
from obsion.capabilities.gateway import CapabilityGateway
from obsion.common.time import utc_now
from obsion.db.models import (
    AuditRecord,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    KnowledgeSyncItem,
    KnowledgeSyncSource,
    Policy,
)
from obsion.domain.enums import (
    Classification,
    DecisionEffect,
)
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.sync import KnowledgeSyncService
from obsion.knowledge.sync_worker import KnowledgeSyncWorker
from obsion.persistence.operator_invocations import operator_request_fingerprint


class Directory(Native):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self.page_tokens: list[str | None] = []

    async def respond(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v2.0/wiki/nodes":
            return await super().respond(request)
        assert request.url.params["parentNodeId"] == "root_test"
        token = request.url.params.get("nextToken")
        self.page_tokens.append(token)
        if token:
            assert token == " opaque + cursor "
            if self.mode == "malformed":
                return httpx.Response(200, json={"nextToken": None})
            return httpx.Response(
                200, json={"nodes": [], "nextToken": token if self.mode == "repeat" else None}
            )
        return httpx.Response(
            200,
            json={
                "nodes": [
                    {
                        "workspaceId": "wiki_test",
                        "nodeId": "doc_one1",
                        "type": "FILE",
                        "extension": "adoc",
                        "hasChildren": False,
                        "permissionRole": "OWNER",
                        "name": "合同流程",
                    }
                ],
                "nextToken": " opaque + cursor ",
            },
        )


async def setup(client: TestClient) -> tuple[Any, Any, Any]:
    async with client.app.state.database.sessions() as session, session.begin():
        principal, connector, binding = await install_fixture(session, client.app.state.settings)
        wrong = Connector(
            organization_id=principal.organization_id,
            name="other organization first",
            connector_type=connector.connector_type,
            status="ACTIVE",
            environment="development",
            configuration={**connector.configuration, "corp_id": "other_corp"},
            credential_ref=connector.credential_ref,
            declared_grants=["knowledge.write"],
            allowed_egress=[],
        )
        session.add(wrong)
        await session.flush()
        for operation in (MANAGED_READ_OPERATION, MANAGED_DISCOVER_OPERATION):
            version = await session.scalar(
                select(CapabilityVersion)
                .join(CapabilityDefinition)
                .where(
                    CapabilityDefinition.organization_id == principal.organization_id,
                    CapabilityDefinition.name == operation,
                )
                .order_by(CapabilityVersion.version.desc())
            )
            assert version is not None
            for candidate in (wrong, connector):
                session.add(
                    CapabilityBinding(
                        organization_id=principal.organization_id,
                        capability_version_id=version.id,
                        connector_id=candidate.id,
                        environment="development",
                        enabled=True,
                        resource_selector={"source": "dingtalk-managed"},
                    )
                )
        session.add(
            Policy(
                organization_id=principal.organization_id,
                name="source worker explicit test allow",
                version=1,
                priority=1,
                effect=DecisionEffect.ALLOW,
                enabled=True,
                conditions={"action": "knowledge.write"},
                obligations=[],
                reason="Test fixture",
                created_by=principal.id,
            )
        )
        await session.flush()
        service = KnowledgeSyncService(
            KnowledgeService(client.app.state.settings, client.app.state.object_store)
        )
        source = await service.create_source(
            session,
            principal,
            connector_id=connector.id,
            binding_id=binding.id,
            correlation_id=uuid4(),
        )
        session.add(
            KnowledgeSyncItem(
                organization_id=principal.organization_id,
                source_id=source.id,
                node_id="older_node",
                workspace_id="wiki_test",
                kind="able",
                title="Old source item",
                status="PARTIAL",
                seen_generation=0,
                gaps=["unsupported_format"],
            )
        )
        return principal, source, service


@pytest.mark.parametrize(
    "mode",
    ["normal", "repeat", "malformed", "denied_body", "empty_body", "stale_worker", "disabled"],
)
def test_worker_resumes_pages_pins_connector_and_fences_publication(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", "app_test")
    monkeypatch.setenv("OBSION_READER_TEST_SECRET", "test-secret")

    async def run() -> None:
        principal, source, service = await setup(client)
        runner, directory = Runner(), Directory(mode)
        if mode == "empty_body":
            original_read = runner.read

            async def empty_read(**kwargs: Any) -> dict[str, Any]:
                data = await original_read(**kwargs)
                data["content"]["jsonml"] = json.dumps(["root", {}])
                return data

            runner.read = empty_read

        gateway = CapabilityGateway(
            {
                "SDK": DingTalkManagedSdkExecutor(
                    runner, transport=httpx.MockTransport(directory.respond)
                )
            }
        )
        database = client.app.state.database
        # Reconstruct the worker for every tick: only committed PostgreSQL/SQLite
        # state, not an in-memory cursor, is allowed to carry the traversal.
        for index in range(6 if mode in {"repeat", "malformed"} else 5):
            if index == 3 and mode == "denied_body":
                directory.role = "READER"
            if index == 3 and mode in {"stale_worker", "disabled"}:
                original = gateway.invoke_operator

                async def interleave(session: Any, request: Any, invoke: Any = original) -> Any:
                    result = await invoke(session, request)
                    async with database.sessions() as other, other.begin():
                        current = await other.get(KnowledgeSyncSource, source.id)
                        if mode == "disabled":
                            await service.disable_source(
                                other, principal, source_id=source.id, correlation_id=uuid4()
                            )
                        else:
                            current.lease_token = uuid4()
                    return result

                gateway.invoke_operator = interleave
            assert await KnowledgeSyncWorker(database, gateway, service).tick()
            async with database.sessions() as session:
                current = await session.get(KnowledgeSyncSource, source.id)
                items = {
                    row.node_id: row
                    for row in (
                        await session.scalars(
                            select(KnowledgeSyncItem).where(
                                KnowledgeSyncItem.source_id == source.id
                            )
                        )
                    ).all()
                }
                if index < 4:
                    assert items["older_node"].status != "MISSING"
                if index == 1:
                    assert current.scan_state["queue"][0]["cursor"] == " opaque + cursor "
                    assert items["doc_one1"].status == "PENDING"
                if index == 2 and mode in {"repeat", "malformed"}:
                    assert current.last_error_code == "dingtalk_docs_response_invalid"
                    assert current.last_success_at is None
                    assert current.scan_state["queue"][0]["cursor"] == " opaque + cursor "
                    assert runner.calls == 0
                    directory.mode = "normal"
                    current.next_poll_at = utc_now()
                    await session.commit()
                    continue
                if index == 3 and mode in {"stale_worker", "disabled"}:
                    assert items["doc_one1"].document_id is None
                    assert current.last_success_at is None
                    return
                if index == 3 and mode == "empty_body":
                    assert items["doc_one1"].document_id is None
                    assert items["doc_one1"].last_error_code == "document_parse_failed"
                    assert current.last_error_code == "document_parse_failed"
                    return
                if index == 3 and mode == "denied_body":
                    assert items["doc_one1"].document_id is None
                    assert items["doc_one1"].access_expires_at is None
                    assert items["doc_one1"].status in {"FAILED", "DENIED"}
                    assert runner.calls == 0
                    return
        expected = [None, " opaque + cursor "] + (
            [" opaque + cursor "] if mode in {"repeat", "malformed"} else []
        )
        assert directory.page_tokens == expected and runner.calls == 1
        async with database.sessions() as session:
            current = await session.get(KnowledgeSyncSource, source.id)
            assert current.scan_state == {} and current.last_success_at is not None
            assert current.last_error_code is None
            items = {
                row.node_id: row
                for row in (
                    await session.scalars(
                        select(KnowledgeSyncItem).where(KnowledgeSyncItem.source_id == source.id)
                    )
                ).all()
            }
            assert items["older_node"].status == "MISSING" and items["doc_one1"].status == "READY"
            assert await service.knowledge.search(session, principal, query="财务复核")
            audits = (
                await session.scalars(
                    select(AuditRecord).where(
                        AuditRecord.organization_id == principal.organization_id
                    )
                )
            ).all()
            assert audits and "test-secret" not in json.dumps(
                [a.redacted_metadata for a in audits], default=str
            )
        assert not await KnowledgeSyncWorker(database, gateway, service).tick()

    client.portal.call(run)


def test_connector_pin_changes_fingerprint_without_breaking_unpinned_replays() -> None:
    import hashlib

    payload: dict[str, Any] = {
        "capability_name": "knowledge.sync",
        "payload": {"document_id": "doc"},
        "resource": {"source": "dingtalk"},
        "environment": "development",
        "context": {},
    }
    previous = {
        "capability": payload["capability_name"],
        **{key: value for key, value in payload.items() if key != "capability_name"},
    }
    digest = hashlib.sha256(
        json.dumps(
            previous, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
    ).hexdigest()
    assert operator_request_fingerprint(**payload) == digest
    assert operator_request_fingerprint(**payload, connector_id=uuid4()) != digest
    assert operator_request_fingerprint(
        **payload, connector_id=uuid4()
    ) != operator_request_fingerprint(**payload, connector_id=uuid4())


@pytest.mark.parametrize("scope", ["item", "source"])
def test_failure_checkpoint_expiring_mid_transaction_rolls_back_every_change(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    from datetime import timedelta

    from obsion.knowledge.sync_queue import checkpoint_source, claim_source

    async def run() -> None:
        principal, source, service = await setup(client)
        database = client.app.state.database
        async with database.sessions() as session, session.begin():
            item = await session.scalar(
                select(KnowledgeSyncItem).where(KnowledgeSyncItem.source_id == source.id)
            )
            document, _, _ = await service.knowledge.ingest(
                session,
                principal,
                source="lease-fence-test",
                external_id="test",
                title="Source lease test",
                media_type="text/plain",
                filename="test.txt",
                content=b"Test source content",
                classification=Classification.INTERNAL,
                acl={"organization": True},
            )
            item.document_id, item.status = document.id, "READY"
            item.checked_at, item.access_expires_at = utc_now(), utc_now() + timedelta(minutes=5)
            claim = await claim_source(session)
            assert claim is not None and claim.id == source.id
            item_id, token = item.id, claim.lease_token

        async def expire_at_checkpoint(session: Any, **kwargs: Any) -> bool:
            current = await session.get(KnowledgeSyncSource, source.id)
            current.lease_expires_at = utc_now() - timedelta(seconds=1)
            await session.flush()
            assert not await checkpoint_source(session, **kwargs)
            return False

        monkeypatch.setattr("obsion.knowledge.sync_worker.checkpoint_source", expire_at_checkpoint)
        worker = KnowledgeSyncWorker(database, CapabilityGateway({}), service)
        await worker._failure(
            claim, {}, "older_node" if scope == "item" else None, "dingtalk_docs_response_invalid"
        )
        async with database.sessions() as session:
            item = await session.get(KnowledgeSyncItem, item_id)
            current = await session.get(KnowledgeSyncSource, source.id)
            assert item.status == "READY" and item.access_expires_at is not None
            assert item.last_error_code is None and current.last_error_code is None
            assert current.lease_token == token and current.scan_state == {}
            assert not (
                await session.scalars(
                    select(AuditRecord).where(
                        AuditRecord.resource_type == "knowledge_source",
                        AuditRecord.resource_id == str(source.id),
                        AuditRecord.outcome == "FAILED",
                    )
                )
            ).all()

    client.portal.call(run)
