"""Synthetic transport through the real Gateway, worker and durable source guards."""

import json

import httpx
import pytest
from sqlalchemy import select
from test_dingtalk_jsonml_layout import cell, item, table
from test_dingtalk_managed_reader import Runner
from test_knowledge_sync_worker import Directory, setup

from obsion.capabilities.dingtalk_managed import DingTalkManagedSdkExecutor
from obsion.capabilities.gateway import CapabilityGateway
from obsion.common.errors import NotFoundError
from obsion.common.time import utc_now
from obsion.db.models import DocumentChunk, KnowledgeSyncItem, KnowledgeSyncSource
from obsion.knowledge.sync_worker import KnowledgeSyncWorker


def test_layout_refresh_versions_body_and_revokes_ambiguous_geometry(client, monkeypatch):
    monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", "app_test")
    monkeypatch.setenv("OBSION_READER_TEST_SECRET", "test-secret")

    async def run():
        principal, source, service = await setup(client)
        runner, directory = Runner(), Directory("normal")
        original = runner.read
        tree = None

        async def read(**kwargs):
            data = await original(**kwargs)
            if tree is not None:
                data["content"]["jsonml"] = json.dumps(tree)
            return data

        runner.read = read
        gateway = CapabilityGateway(
            {
                "SDK": DingTalkManagedSdkExecutor(
                    runner, transport=httpx.MockTransport(directory.respond)
                )
            }
        )
        database = client.app.state.database

        async def cycle():
            async with database.sessions() as session, session.begin():
                current = await session.get(KnowledgeSyncSource, source.id)
                current.next_poll_at = utc_now()
            for _ in range(5):
                assert await KnowledgeSyncWorker(database, gateway, service).tick()

        async def source_item(session):
            return await session.scalar(
                select(KnowledgeSyncItem).where(
                    KnowledgeSyncItem.source_id == source.id,
                    KnowledgeSyncItem.node_id == "doc_one1",
                )
            )

        await cycle()
        async with database.sessions() as session:
            current = await source_item(session)
            doc_id = current.document_id
            _, version = await service.knowledge.get_document(session, principal, doc_id)
            assert version.version == 1
        valid = [
            "root",
            {},
            item("Approve"),
            item("Archive"),
            table([cell("Travel", colSpan=2), cell(hidden=True)], [cell("Rail"), cell("200")]),
        ]
        tree = valid
        await cycle()
        async with database.sessions() as session:
            current = await source_item(session)
            assert current.status == "READY" and current.document_id == doc_id
            _, version = await service.knowledge.get_document(session, principal, doc_id)
            assert version.version == 2
            assert version.metadata_json["source_parser"] == "dingtalk-jsonml-v2"
            chunks = (
                await session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_version_id == version.id)
                    .order_by(DocumentChunk.ordinal)
                )
            ).all()
            assert chunks[0].content == "1. Approve\n\n2. Archive"
            assert len(chunks) == 2 and chunks[1].content.startswith("<table>\n")
            assert chunks[1].content.endswith("\n</table>")
        tree = ["root", {}, table([cell("Travel", colSpan=2), cell("Occupied")])]
        await cycle()
        async with database.sessions() as session:
            current = await source_item(session)
            assert current.status == "PARTIAL" and current.access_expires_at is None
            with pytest.raises(NotFoundError):
                await service.knowledge.get_document(session, principal, doc_id)
        tree = valid
        await cycle()
        async with database.sessions() as session:
            current = await source_item(session)
            assert current.status == "READY" and current.document_id == doc_id
            _, version = await service.knowledge.get_document(session, principal, doc_id)
            assert version.version == 2

    client.portal.call(run)
