"""Live mode uses fresh external acquisition, never synthetic index fallback.

Provider responses here are explicit test adapters; these tests do not claim
real tenant connectivity or semantic answer accuracy.
"""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_dingtalk_managed_reader import Runner, envelope
from test_knowledge_sync_worker import Directory, setup
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.capabilities.dingtalk_managed import DingTalkManagedSdkExecutor
from obsion.capabilities.gateway import CapabilityGateway
from obsion.common.errors import NotFoundError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Environment, Settings
from obsion.db.models import Evidence, KnowledgeSyncItem, KnowledgeSyncSource
from obsion.domain.enums import Classification
from obsion.knowledge.document_read import read_document_page
from obsion.knowledge.live import LiveKnowledgeProof, LiveKnowledgeUnavailable, await_live_sources
from obsion.knowledge.sync_queue import checkpoint_source, claim_source
from obsion.knowledge.sync_worker import KnowledgeSyncWorker
from obsion.model_gateway.gateway import ModelGateway, ModelResult


def test_live_is_default_and_indexed_mode_is_never_production():
    assert Settings(_env_file=None).enterprise_knowledge_mode == "live"
    for environment in (Environment.STAGING, Environment.PRODUCTION):
        with pytest.raises(ValidationError, match="Indexed development"):
            Settings(
                _env_file=None,
                environment=environment,
                enterprise_knowledge_mode="indexed-development",
                auth_mode="oidc",
                allowed_origins=["https://app.example.invalid"],
                oidc_issuer="https://identity.example.invalid",
                oidc_audience="obsion",
                oidc_jwks_url="https://identity.example.invalid/jwks",
            )


@pytest.mark.parametrize("selected", [False, True])
def test_normal_question_does_not_use_available_native_index_or_model(
    client, monkeypatch, selected
):
    client.app.state.settings.enterprise_knowledge_mode = "live"
    client.app.state.settings.knowledge_live_wait_seconds = 1
    created = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("policy.txt", "差旅报销须经过财务复核。".encode(), "text/plain")},
        data={
            "source": "explicit-synthetic-test",
            "external_id": "stale-policy",
            "title": "差旅报销制度",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert created.status_code == 201, created.text

    async def no_model(*args, **kwargs):
        if not selected:
            pytest.fail("Unavailable live knowledge must not reach answer or investigation models")
        return ModelResult(
            content=json.dumps({"answerable": False, "answer": "", "claims": []}),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            cost_amount=Decimal("0"),
            latency_ms=1,
            finish_reason="stop",
        )

    monkeypatch.setattr(ModelGateway, "complete", no_model)
    thread = _create_thread(client)
    payload = {"input": "公司差旅报销制度是什么？"}
    if selected:
        payload["context_refs"] = [
            {"type": "task_context", "document_ids": [created.json()["document"]["id"]]}
        ]
    response = client.post(f"/api/v1/threads/{thread['id']}/turns", json=payload)
    assert response.status_code == 202, response.text
    run = _wait_terminal(client, response.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    run = client.get(f"/api/v1/runs/{run['id']}").json()
    if selected:
        assert "knowledge_live" not in run["plan"]

        async def selected_evidence():
            from uuid import UUID

            async with client.app.state.database.sessions() as session:
                rows = list(
                    await session.scalars(
                        select(Evidence).where(Evidence.run_id == UUID(run["id"]))
                    )
                )
                hits = [hit for row in rows for hit in row.content.get("hits", [])]
                assert hits and {hit["document_id"] for hit in hits} == {
                    created.json()["document"]["id"]
                }
                assert all(hit["source"] == "explicit-synthetic-test" for hit in hits)

        client.portal.call(selected_evidence)
        return
    assert run["plan"]["knowledge_live"] == {"status": "BLOCKED", "reason": "source_not_configured"}
    assert run["plan"]["answer_generation"]["status"] == "SOURCE_UNAVAILABLE"
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(a["inline_content"] for a in artifacts if a["title"] == "Obsion answer")
    assert "未能完成企业来源的实时查证" in answer["markdown"]
    assert "财务复核" not in answer["markdown"]


@pytest.mark.parametrize("mode", ["old_scan", "future_scan", "legacy", "error", "leased"])
def test_stale_or_unavailable_scans_cannot_be_freshness_receipts(client, mode):
    async def run():
        principal, source, _ = await setup(client)
        database = client.app.state.database
        requested = utc_now() - timedelta(seconds=1)
        async with database.sessions() as session, session.begin():
            current = await session.get(KnowledgeSyncSource, source.id)
            current.generation = 3
            current.last_success_at = utc_now()
            if mode in {"old_scan", "future_scan"}:
                current.completed_generation = 3
                start = requested - timedelta(seconds=1)
                if mode == "future_scan":
                    start = utc_now() + timedelta(minutes=2)
                    current.last_success_at = start + timedelta(seconds=1)
                current.completed_scan_started_at = start
            if mode == "error":
                current.last_error_code = "dingtalk_docs_upstream_denied"
            current.next_poll_at = utc_now() + timedelta(minutes=5)
            current.scan_state = {"stage": "READ", "cursor": "saved"}
            current.scan_started_at = requested - timedelta(seconds=5)
            token = uuid4()
            if mode == "leased":
                current.lease_token = token
                current.lease_expires_at = utc_now() + timedelta(minutes=1)
        with pytest.raises(
            LiveKnowledgeUnavailable,
            match=("source_refresh_failed" if mode == "error" else "source_refresh_timeout"),
        ):
            await await_live_sources(database, principal, requested_at=requested, wait_seconds=0.02)
        async with database.sessions() as session:
            current = await session.get(KnowledgeSyncSource, source.id)
            assert current.generation == 3
            assert current.scan_state == {"stage": "READ", "cursor": "saved"}
            assert ensure_utc(current.scan_started_at) < requested
            if mode == "leased":
                assert current.lease_token == token
                assert ensure_utc(current.next_poll_at) > utc_now()
            else:
                assert ensure_utc(current.next_poll_at) <= utc_now()

    client.portal.call(run)


@pytest.mark.parametrize("changed", [False, True])
def test_each_question_requires_new_governed_reads_even_when_content_unchanged(
    client, monkeypatch, changed
):
    monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", "app_test")
    monkeypatch.setenv("OBSION_READER_TEST_SECRET", "test-secret")

    async def run():
        principal, source, service = await setup(client)
        monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", source.app_key)
        database = client.app.state.database
        runner, directory = Runner(), Directory("normal")
        gateway = CapabilityGateway(
            {
                "SDK": DingTalkManagedSdkExecutor(
                    runner, transport=httpx.MockTransport(directory.respond)
                )
            }
        )
        worker = KnowledgeSyncWorker(database, gateway, service)
        async with database.sessions() as session, session.begin():
            await service.knowledge.ingest(
                session,
                principal,
                source="native-test",
                external_id="native",
                title="Native fixture",
                media_type="text/plain",
                filename="native.txt",
                content="财务复核：这是未经本轮外部查证的本地答案。".encode(),
                classification=Classification.INTERNAL,
                acl={"organization": True},
            )
        first_version = None
        for question in range(2):
            if changed and question == 1:
                original_read = runner.read

                async def changed_read(original=original_read, **kwargs):
                    await original(**kwargs)
                    result = envelope(
                        ["root", {}, ["p", {}, ["span", {}, "财务复核新增二次确认。"]]]
                    )
                    result["content"]["revision"] = "18"
                    return result

                runner.read = changed_read
            requested = utc_now()
            task = asyncio.create_task(
                await_live_sources(
                    database,
                    principal,
                    requested_at=requested,
                    wait_seconds=8,
                )
            )
            try:
                while not task.done():
                    await worker.tick()
                    await asyncio.sleep(0.01)
                proof = await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert runner.calls == question + 1
            assert proof.sources[0].generation == question + 1
            assert proof.sources[0].started_at >= requested
            # Repeated searches within this same question may use its fresh acquisition.
            again = await await_live_sources(
                database, principal, requested_at=requested, wait_seconds=0
            )
            assert again == proof and runner.calls == question + 1
            async with database.sessions() as session:
                hits = await service.knowledge.search(
                    session, principal, "财务复核", live_proof=proof
                )
                assert hits and {hit.source for hit in hits} == {"dingtalk-managed"}
                assert all("本地答案" not in hit.content for hit in hits)
                first_version = first_version or hits[0].version
                if changed and question == 1:
                    assert hits[0].version > first_version
                    assert any("二次确认" in hit.content for hit in hits)
                else:
                    assert hits[0].version == first_version
                page = await read_document_page(
                    session,
                    principal,
                    {
                        "document_id": str(hits[0].document_id),
                        "version": hits[0].version,
                        "offset": 0,
                        "limit": 4,
                    },
                    live_proof=proof,
                )
                assert page["hits"]
                assert not await service.knowledge.search(
                    session,
                    principal,
                    "财务复核",
                    live_proof=LiveKnowledgeProof(requested, (), 0),
                )
        async with database.sessions() as session, session.begin():
            item = await session.scalar(
                select(KnowledgeSyncItem).where(
                    KnowledgeSyncItem.source_id == source.id, KnowledgeSyncItem.status == "READY"
                )
            )
            item.read_generation = proof.sources[0].generation - 1
        async with database.sessions() as session:
            assert not await service.knowledge.search(
                session, principal, "财务复核", live_proof=proof
            )
            with pytest.raises(NotFoundError):
                await read_document_page(
                    session,
                    principal,
                    {
                        "document_id": str(hits[0].document_id),
                        "version": hits[0].version,
                    },
                    live_proof=proof,
                )

    client.portal.call(run)


def assert_live_harness_can_wait_while_external_worker_commits(client, monkeypatch):
    # Executed by the opt-in PostgreSQL suite: SQLite serializes all writers and
    # cannot represent the repository's concurrent control-plane/worker contract.
    assert client.app.state.database.engine.dialect.name == "postgresql"
    client.app.state.settings.enterprise_knowledge_mode = "live"
    client.app.state.settings.knowledge_live_wait_seconds = 5
    monkeypatch.setenv("OBSION_READER_TEST_SECRET", "test-secret")
    runner, directory = Runner(), Directory("normal")

    async def start():
        _, source, service = await setup(client)
        monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", source.app_key)
        worker = KnowledgeSyncWorker(
            client.app.state.database,
            CapabilityGateway(
                {
                    "SDK": DingTalkManagedSdkExecutor(
                        runner, transport=httpx.MockTransport(directory.respond)
                    )
                }
            ),
            service,
        )

        async def pump():
            while True:
                await worker.tick()
                await asyncio.sleep(0.01)

        return asyncio.create_task(pump())

    observed = []

    async def model(self, session, **kwargs):
        evidence = list(
            await session.scalars(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        )
        assert any(row.content.get("freshness", {}).get("status") == "READY" for row in evidence)
        observed.append(True)
        return ModelResult(
            content=json.dumps(
                {"answerable": False, "answer": "", "claims": [], "missing_information": "审批要求"}
            ),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            cost_amount=Decimal("0"),
            latency_ms=1,
            finish_reason="stop",
        )

    monkeypatch.setattr(ModelGateway, "complete", model)
    pump = client.portal.call(start)
    try:
        workspace = client.post(
            "/api/v1/workspaces", json={"name": "Live acquisition", "classification": "RESTRICTED"}
        )
        assert workspace.status_code == 201
        thread = client.post(
            "/api/v1/threads",
            json={"workspace_id": workspace.json()["id"], "title": "Live acquisition"},
        ).json()
        created = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"input": "公司报价审批制度要求财务复核吗？"},
        )
        assert created.status_code == 202, created.text
        run = _wait_terminal(client, created.json()["run"]["id"])
        assert run["status"] == "COMPLETED", run
        assert run["plan"]["knowledge_live"]["status"] == "READY", run["plan"]
        assert runner.calls >= 1 and observed
    finally:

        async def stop():
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

        client.portal.call(stop)


def test_waiter_rejects_source_scope_change_and_cancels_cleanly(client, monkeypatch):
    async def run():
        principal, source, _ = await setup(client)
        database = client.app.state.database
        original_sleep = asyncio.sleep
        touched = False

        async def change_source(_):
            nonlocal touched
            touched = True
            async with database.sessions() as session, session.begin():
                current = await session.get(KnowledgeSyncSource, source.id)
                current.active = False

        monkeypatch.setattr("obsion.knowledge.live.asyncio", SimpleNamespace(sleep=change_source))
        with pytest.raises(
            LiveKnowledgeUnavailable, match="source_not_configured|source_scope_changed"
        ):
            await await_live_sources(database, principal, requested_at=utc_now(), wait_seconds=1)
        assert touched
        monkeypatch.setattr("obsion.knowledge.live.asyncio", asyncio)
        async with database.sessions() as session, session.begin():
            current = await session.get(KnowledgeSyncSource, source.id)
            current.active = True
        task = asyncio.create_task(
            await_live_sources(database, principal, requested_at=utc_now(), wait_seconds=5)
        )
        await original_sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with database.sessions() as session:
            current = await session.get(KnowledgeSyncSource, source.id)
            assert current.lease_token is None and current.generation == 0

    client.portal.call(run)


def test_resume_preserves_actual_scan_start_and_legacy_scan_has_no_receipt(client):
    async def run():
        _, source, _ = await setup(client)
        database = client.app.state.database
        complete = {"stage": "COMPLETE", "queue": [], "cursor": None, "enumeration_complete": True}
        async with database.sessions() as session, session.begin():
            claim = await claim_source(session)
            start = claim.scan_started_at
            assert start is not None
            assert await checkpoint_source(
                session,
                source_id=source.id,
                lease_token=claim.lease_token,
                generation=claim.generation,
                scan_state={"stage": "READ", "queue": []},
            )
        async with database.sessions() as session, session.begin():
            claim = await claim_source(session)
            assert claim.generation == 1 and ensure_utc(claim.scan_started_at) == ensure_utc(start)
            assert await checkpoint_source(
                session,
                source_id=source.id,
                lease_token=claim.lease_token,
                generation=claim.generation,
                scan_state=complete,
                complete=True,
            )
            assert claim.completed_generation == 1
            assert ensure_utc(claim.completed_scan_started_at) == ensure_utc(start)
            claim.next_poll_at = utc_now()
        async with database.sessions() as session, session.begin():
            claim = await claim_source(session)
            assert claim.generation == 2 and ensure_utc(claim.scan_started_at) > ensure_utc(start)
            # A scan already in progress during migration cannot invent its start.
            claim.scan_started_at = None
            await session.flush()
            assert await checkpoint_source(
                session,
                source_id=source.id,
                lease_token=claim.lease_token,
                generation=claim.generation,
                scan_state=complete,
                complete=True,
            )
            assert claim.completed_generation is None and claim.completed_scan_started_at is None

    client.portal.call(run)


@pytest.mark.parametrize(
    "mode", ["missing_generation", "missing_start", "future_completion", "zero", "ahead"]
)
def test_database_rejects_inconsistent_receipts(client, mode):
    async def run():
        _, source, _ = await setup(client)
        with pytest.raises(IntegrityError):
            async with client.app.state.database.sessions() as session, session.begin():
                current = await session.get(KnowledgeSyncSource, source.id)
                current.generation = 1
                current.completed_generation = None if mode == "missing_generation" else 1
                current.completed_scan_started_at = None if mode == "missing_start" else utc_now()
                current.last_success_at = (
                    utc_now() - timedelta(minutes=1) if mode == "future_completion" else utc_now()
                )
                if mode == "zero":
                    current.completed_generation = 0
                elif mode == "ahead":
                    current.completed_generation = 2

    client.portal.call(run)
