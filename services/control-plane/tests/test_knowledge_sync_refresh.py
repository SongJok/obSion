"""A routine rescan must not revoke a still-valid verified source lease."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_dingtalk_managed_reader import Runner
from test_knowledge_sync_worker import Directory, setup

from obsion.capabilities.dingtalk_managed import DingTalkManagedSdkExecutor
from obsion.capabilities.gateway import CapabilityGateway
from obsion.common.errors import NotFoundError
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import KnowledgeSyncItem, KnowledgeSyncSource
from obsion.knowledge.sync_worker import KnowledgeSyncWorker


@pytest.mark.parametrize("mode", ["normal", "expired", "denied", "partial", "pause", "missing"])
def test_periodic_refresh_preserves_access_without_renewing_it(client, monkeypatch, mode):
    monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", "app_test")
    monkeypatch.setenv("OBSION_READER_TEST_SECRET", "test-secret")

    async def run():
        principal, source, service = await setup(client)
        runner, directory = Runner(), Directory("normal")
        missing = False

        async def respond(request):
            if missing and request.url.path == "/v2.0/wiki/nodes":
                return httpx.Response(200, json={"nodes": [], "nextToken": None})
            return await directory.respond(request)

        gateway = CapabilityGateway(
            {"SDK": DingTalkManagedSdkExecutor(runner, transport=httpx.MockTransport(respond))}
        )
        database = client.app.state.database

        async def tick():
            assert await KnowledgeSyncWorker(database, gateway, service).tick()

        async def due():
            async with database.sessions() as session, session.begin():
                current = await session.get(KnowledgeSyncSource, source.id)
                current.next_poll_at = utc_now()

        async def item_in(session):
            return await session.scalar(
                select(KnowledgeSyncItem).where(
                    KnowledgeSyncItem.source_id == source.id,
                    KnowledgeSyncItem.node_id == "doc_one1",
                )
            )

        for _ in range(5):
            await tick()
        async with database.sessions() as session, session.begin():
            item = await item_in(session)
            doc_id = item.document_id
            document, version = await service.knowledge.get_document(session, principal, doc_id)
            original_version = version.version
            if mode == "expired":
                item.checked_at = utc_now() - timedelta(minutes=6)
                item.access_expires_at = utc_now() - timedelta(seconds=1)
            original_expiry = ensure_utc(item.access_expires_at)
        await due()
        await tick()  # New generation, workspace discovery.
        if mode == "missing":
            missing = True
        await tick()  # Rediscovery alone is not a revocation or a lease renewal.
        async with database.sessions() as session:
            item = await item_in(session)
            assert item.status == "READY"
            assert ensure_utc(item.access_expires_at) == original_expiry
            if mode == "expired":
                with pytest.raises(NotFoundError):
                    await service.knowledge.get_document(session, principal, doc_id)
            else:
                assert await service.knowledge.get_document(session, principal, doc_id)
        if mode == "pause":
            async with database.sessions() as session, session.begin():
                await service.disable_source(
                    session, principal, source_id=source.id, correlation_id=uuid4()
                )
                with pytest.raises(NotFoundError):
                    await service.knowledge.get_document(session, principal, doc_id)
            assert not await KnowledgeSyncWorker(database, gateway, service).tick()
            return
        if mode != "missing":
            await tick()  # Exhaust the node page cursor.
            if mode == "denied":
                directory.role = "READER"
            if mode == "partial":
                original_read = runner.read

                async def partial_read(**kwargs):
                    data = await original_read(**kwargs)
                    import json

                    data["content"]["jsonml"] = json.dumps(["root", {}, ["image", {}]])
                    return data

                runner.read = partial_read
            await tick()  # Fresh body/permission check, even when status stayed READY.
            await due()
        await tick()  # Complete; only an exhausted scan may mark missing nodes.
        async with database.sessions() as session:
            current = await session.get(KnowledgeSyncSource, source.id)
            item = await item_in(session)
            assert current.scan_state == {}
            if mode in {"denied", "partial", "missing"}:
                assert (
                    item.status
                    == {"denied": "DENIED", "partial": "PARTIAL", "missing": "MISSING"}[mode]
                )
                assert item.access_expires_at is None
                with pytest.raises(NotFoundError):
                    await service.knowledge.get_document(session, principal, doc_id)
            else:
                assert (
                    item.status == "READY" and ensure_utc(item.access_expires_at) > original_expiry
                )
                _, version = await service.knowledge.get_document(session, principal, doc_id)
                assert version.version == original_version
                assert runner.calls == 2
            if mode != "missing":
                assert item.read_generation == current.generation
        assert not await KnowledgeSyncWorker(database, gateway, service).tick()

    client.portal.call(run)
