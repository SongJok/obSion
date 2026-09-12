"""Real PostgreSQL ordering tests; opt-in disposable database only."""

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from obsion.common.errors import ObsionError
from obsion.db.models import Organization, User
from obsion.security.source_fence import acquire_source_publication_fence


@pytest_asyncio.fixture
async def fence_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Source fence concurrency requires explicit disposable PostgreSQL")
    url = os.environ["OBSION_DATABASE_URL"]
    assert url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(url, pool_size=5)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def seed(sessions: async_sessionmaker[AsyncSession]) -> tuple[UUID, UUID]:
    async with sessions() as session, session.begin():
        organization = Organization(slug=f"source-fence-{uuid4()}", name="Fence test")
        session.add(organization)
        await session.flush()
        user = User(
            organization_id=organization.id,
            external_id=str(uuid4()),
            email="source-fence@example.invalid",
            display_name="Synthetic fence subject",
            active=True,
        )
        session.add(user)
        await session.flush()
        return organization.id, user.id


async def wait_blocked(
    sessions: async_sessionmaker[AsyncSession], waiter: int, blocker: int
) -> None:
    async with sessions() as session:
        for _ in range(100):
            pids = await session.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter})
            if blocker in pids:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("Expected a live PostgreSQL lock wait, not elapsed-time inference")


@pytest.mark.parametrize("revoked", [False, True])
def test_metadata_permission_audit_does_not_wait_for_model_run_lock(
    fence_client, monkeypatch, revoked
):
    _assert_read_audit_does_not_wait(fence_client, monkeypatch, revoked, "metadata")


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize(
    "target", ["artifacts", "events", "workspace_artifacts", "workspace_evidence", "timeline"]
)
def test_content_permission_audit_does_not_wait_for_model_run_lock(
    fence_client, monkeypatch, revoked, target
):
    _assert_read_audit_does_not_wait(fence_client, monkeypatch, revoked, target)


def _assert_read_audit_does_not_wait(client, monkeypatch, revoked, target):
    from test_managed_answer_access import test_managed_access_is_rechecked_across_model_work

    from obsion.db.models import AuditRecord, KnowledgeSyncSource, PolicyDecision, Run, Thread, Turn

    test_managed_access_is_rechecked_across_model_work(client, monkeypatch, "none")

    async def check():
        sessions = client.app.state.database.sessions
        organization = client.app.state.settings.dev_organization_id
        async with sessions() as session, session.begin():
            run_id = await session.scalar(select(Run.id).where(Run.organization_id == organization))
            workspace_id = await session.scalar(
                select(Thread.workspace_id).join(Turn).join(Run).where(Run.id == run_id)
            )
            paths = {
                "metadata": (f"/runs/{run_id}", "run_metadata"),
                "artifacts": (f"/runs/{run_id}/artifacts", "historical_content"),
                "events": (f"/runs/{run_id}/events", "historical_content"),
                "workspace_artifacts": (
                    f"/workspaces/{workspace_id}/artifacts",
                    "artifact_content",
                ),
                "workspace_evidence": (
                    f"/workspaces/{workspace_id}/evidence",
                    "workspace_evidence",
                ),
                "timeline": (f"/workspaces/{workspace_id}/timeline", "workspace_timeline"),
            }
            path, stage = paths[target]
            if revoked:
                await session.execute(
                    update(KnowledgeSyncSource)
                    .where(KnowledgeSyncSource.organization_id == organization)
                    .values(active=False)
                )
        async with sessions() as holder, sessions() as monitor:
            await holder.execute(select(Run).where(Run.id == run_id).with_for_update())
            holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
            pending = asyncio.create_task(asyncio.to_thread(client.get, "/api/v1" + path))
            blocked = False
            try:
                for _ in range(200):
                    if pending.done():
                        break
                    blocked = bool(
                        await monitor.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                            ),
                            {"pid": holder_pid},
                        )
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                completed_while_locked = pending.done()
            finally:
                await holder.rollback()
            response = await asyncio.wait_for(pending, 3)
            assert not blocked, f"{target} Policy audit is blocked by the model's Run row lock"
            assert completed_while_locked
            if target == "metadata":
                assert response.status_code == 200
                assert response.json()["source_content_available"] is (not revoked)
                if revoked:
                    assert response.json()["plan"] == {} and response.json()["intent"] == {}
            elif revoked and target in {"artifacts", "events"}:
                assert response.status_code == 404
            else:
                assert response.status_code == 200
                if revoked:
                    assert response.json() == []
                else:
                    assert response.json()
        async with sessions() as session:
            audit = await session.scalar(
                select(AuditRecord)
                .where(
                    AuditRecord.correlation_id == run_id,
                    AuditRecord.action == "knowledge.read",
                    AuditRecord.outcome == ("DENIED" if revoked else "AUTHORIZED"),
                )
                .order_by(AuditRecord.created_at.desc())
            )
            assert audit is not None and audit.policy_decision_id is not None
            decision = await session.get(PolicyDecision, audit.policy_decision_id)
            assert decision.run_id is None
            assert decision.resource["run_id"] == str(run_id)
            assert decision.context["stage"] == stage

    client.portal.call(check)


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["commit", "rollback"])
async def test_publication_fence_orders_direct_revocation_until_transaction_end(
    fence_sessions: async_sessionmaker[AsyncSession], release: str
) -> None:
    organization, user_id = await seed(fence_sessions)
    async with fence_sessions() as publisher, fence_sessions() as revoker:
        await acquire_source_publication_fence(publisher, organization)
        publisher_pid = await publisher.scalar(text("SELECT pg_backend_pid()"))
        revoker_pid = await revoker.scalar(text("SELECT pg_backend_pid()"))

        async def revoke() -> None:
            await revoker.execute(update(User).where(User.id == user_id).values(active=False))
            await revoker.commit()

        pending = asyncio.create_task(revoke())
        try:
            await wait_blocked(fence_sessions, revoker_pid, publisher_pid)
            assert not pending.done()
            assert await publisher.scalar(select(User.active).where(User.id == user_id)) is True
            await getattr(publisher, release)()
            await asyncio.wait_for(pending, 2)
            assert await publisher.scalar(select(User.active).where(User.id == user_id)) is False
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_revocation_first_is_visible_after_publisher_waits(
    fence_sessions: async_sessionmaker[AsyncSession],
) -> None:
    organization, user_id = await seed(fence_sessions)
    async with fence_sessions() as publisher, fence_sessions() as revoker:
        # Establish an earlier statement snapshot before the concurrent change.
        assert await publisher.scalar(select(User.active).where(User.id == user_id)) is True
        await revoker.execute(update(User).where(User.id == user_id).values(active=False))
        publisher_pid = await publisher.scalar(text("SELECT pg_backend_pid()"))
        revoker_pid = await revoker.scalar(text("SELECT pg_backend_pid()"))
        pending = asyncio.create_task(acquire_source_publication_fence(publisher, organization))
        try:
            await wait_blocked(fence_sessions, publisher_pid, revoker_pid)
            await revoker.commit()
            await asyncio.wait_for(pending, 2)
            assert await publisher.scalar(select(User.active).where(User.id == user_id)) is False
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_same_organization_publishers_and_other_organization_writes_do_not_block(
    fence_sessions: async_sessionmaker[AsyncSession],
) -> None:
    organization, _ = await seed(fence_sessions)
    other_organization, other_user = await seed(fence_sessions)
    assert other_organization != organization
    async with fence_sessions() as first, fence_sessions() as second:
        await acquire_source_publication_fence(first, organization)
        await asyncio.wait_for(acquire_source_publication_fence(second, organization), 1)
        await asyncio.wait_for(
            second.execute(update(User).where(User.id == other_user).values(active=False)), 1
        )
        await second.commit()


@pytest.mark.asyncio
async def test_busy_fence_is_bounded_and_does_not_abort_callers_transaction(
    fence_sessions: async_sessionmaker[AsyncSession],
) -> None:
    organization, user_id = await seed(fence_sessions)
    async with fence_sessions() as publisher, fence_sessions() as revoker:
        await revoker.execute(update(User).where(User.id == user_id).values(active=False))
        previous_timeout = await publisher.scalar(text("SHOW lock_timeout"))
        with pytest.raises(ObsionError) as failure:
            await asyncio.wait_for(acquire_source_publication_fence(publisher, organization), 4)
        assert failure.value.code == "authorization_fence_unavailable"
        assert await publisher.scalar(text("SELECT 1")) == 1
        assert await publisher.scalar(text("SHOW lock_timeout")) == previous_timeout
        await revoker.rollback()
        await acquire_source_publication_fence(publisher, organization)


@pytest.mark.asyncio
@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
async def test_old_transaction_snapshot_is_not_accepted_as_current_authorization(
    fence_sessions: async_sessionmaker[AsyncSession], isolation: str
) -> None:
    organization, _ = await seed(fence_sessions)
    async with fence_sessions() as publisher:
        await publisher.connection(execution_options={"isolation_level": isolation})
        with pytest.raises(ObsionError) as failure:
            await acquire_source_publication_fence(publisher, organization)
        assert failure.value.code == "authorization_fence_unavailable"


@pytest.fixture
def fence_client(monkeypatch):
    from fastapi.testclient import TestClient
    from test_dingtalk_managed_reader import install_fixture

    from obsion.config import Environment, Settings
    from obsion.main import create_app

    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Managed publication race requires explicit disposable PostgreSQL")
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        database_url=os.environ["OBSION_DATABASE_URL"],
        dev_organization_id=uuid4(),
        dev_user_id=uuid4(),
        dev_bearer_token="source-fence-isolated-test",
        allowed_origins=["http://testserver"],
        model_allowed_ids=["kimi-k3-kimi", "kimi-k2.7-kimi"],
    )

    async def isolated_install(session, settings):
        return await install_fixture(
            session, settings, app_key=f"app_{settings.dev_organization_id}"
        )

    monkeypatch.setattr("test_knowledge_sync_worker.install_fixture", isolated_install)
    with TestClient(
        create_app(settings), headers={"Authorization": "Bearer source-fence-isolated-test"}
    ) as client:
        yield client


class PendingSourceRevocation:
    """A real SQL writer, held before commit only so assertions can observe order."""

    def __init__(self, sessions, organization, publisher_pid):
        self.sessions = sessions
        self.organization = organization
        self.publisher_pid = publisher_pid
        self.ready = asyncio.Event()
        self.written = asyncio.Event()
        self.commit_allowed = asyncio.Event()
        self.pid = None
        self.task = asyncio.create_task(self._write())

    async def _write(self):
        from obsion.db.models import KnowledgeSyncSource

        async with self.sessions() as session, session.begin():
            self.pid = await session.scalar(text("SELECT pg_backend_pid()"))
            self.ready.set()
            await session.execute(
                update(KnowledgeSyncSource)
                .where(KnowledgeSyncSource.organization_id == self.organization)
                .values(active=False)
            )
            self.written.set()
            await self.commit_allowed.wait()

    async def prove_blocked(self):
        await asyncio.wait_for(self.ready.wait(), 2)
        await wait_blocked(self.sessions, self.pid, self.publisher_pid)
        assert not self.written.is_set()

    async def commit(self):
        await asyncio.wait_for(self.written.wait(), 2)
        self.commit_allowed.set()
        await asyncio.wait_for(self.task, 2)

    async def close(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


def test_real_harness_publication_commits_before_waiting_source_revocation(
    fence_client, monkeypatch
):
    from test_managed_answer_access import test_managed_access_is_rechecked_across_model_work

    from obsion.db.models import Artifact, Run
    from obsion.knowledge.publication import KnowledgePublicationGuard

    client = fence_client
    original = KnowledgePublicationGuard.check
    pending = []
    run_ids = []

    async def checkpoint(self, session, principal, evidence, *, run_id, stage, **kwargs):
        allowed = await original(
            self, session, principal, evidence, run_id=run_id, stage=stage, **kwargs
        )
        if stage == "before_publication" and allowed and not pending:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            pending.append(
                PendingSourceRevocation(
                    client.app.state.database.sessions, principal.organization_id, pid
                )
            )
            run_ids.append(run_id)
            await pending[0].prove_blocked()
        return allowed

    monkeypatch.setattr(KnowledgePublicationGuard, "check", checkpoint)
    try:
        test_managed_access_is_rechecked_across_model_work(client, monkeypatch, "none")
        assert pending

        async def published_before_revoke():
            async with client.app.state.database.sessions() as session:
                assert (
                    await session.scalar(select(Run.status).where(Run.id == run_ids[0]))
                    == "COMPLETED"
                )
                assert await session.scalar(
                    select(Artifact.id).where(Artifact.run_id == run_ids[0])
                )
            await pending[0].commit()

        client.portal.call(published_before_revoke)
        assert client.get(f"/api/v1/runs/{run_ids[0]}/artifacts").status_code == 404
    finally:
        if pending:
            client.portal.call(pending[0].close)


def test_claimed_outbox_holds_fence_through_gateway_result_and_commit(fence_client, monkeypatch):
    from managed_outbox_fixture import queued_managed_reply
    from test_managed_answer_access import test_managed_access_is_rechecked_across_model_work

    from obsion.capabilities.dingtalk_robot import RobotSendResult, RobotSendState
    from obsion.capabilities.gateway import DingTalkRobotOutboxResult
    from obsion.db.models import DingTalkRobotOutbox, KnowledgeSyncSource, Run

    client = fence_client
    test_managed_access_is_rechecked_across_model_work(client, monkeypatch, "none")
    pending = []

    async def dispatch():
        async with client.app.state.database.sessions() as session:
            run_id = await session.scalar(
                select(Run.id).where(
                    Run.status == "COMPLETED",
                    Run.organization_id == client.app.state.settings.dev_organization_id,
                )
            )
        service, gateway, claim, _ = await queued_managed_reply(client, run_id)

        async with client.app.state.database.sessions() as revoker:
            await revoker.execute(
                update(KnowledgeSyncSource)
                .where(
                    KnowledgeSyncSource.organization_id
                    == client.app.state.settings.dev_organization_id
                )
                .values(active=False)
            )
            async with client.app.state.database.sessions() as session, session.begin():
                blocked = await service.dispatch(session, claim)
                assert blocked.status == "BLOCKED"
                assert blocked.last_error_code == "authorization_fence_unavailable"
                assert blocked.lease_expires_at is None
            gateway.invoke_dingtalk_robot_outbox.assert_not_awaited()
            await revoker.rollback()
        async with client.app.state.database.sessions() as session, session.begin():
            assert await service.enqueue_next(session) is not None
        async with client.app.state.database.sessions() as session, session.begin():
            next_claim = await service.claim_next(session)
            assert next_claim is not None and next_claim.outbox_id == claim.outbox_id
            claim = next_claim

        async def send(session, request, **_kwargs):
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            pending.append(
                PendingSourceRevocation(
                    client.app.state.database.sessions, request.principal.organization_id, pid
                )
            )
            await pending[0].prove_blocked()
            return DingTalkRobotOutboxResult(
                RobotSendResult(
                    RobotSendState.ACCEPTED, process_query_key="synthetic-fence-receipt"
                ),
                None,
                request.capability_version_id,
                request.connector_id,
            )

        gateway.invoke_dingtalk_robot_outbox.side_effect = send
        async with client.app.state.database.sessions() as session, session.begin():
            result = await service.dispatch(session, claim)
            assert result.status == "ACCEPTED"
        async with client.app.state.database.sessions() as session:
            assert (
                await session.scalar(
                    select(DingTalkRobotOutbox.status).where(
                        DingTalkRobotOutbox.id == claim.outbox_id
                    )
                )
                == "ACCEPTED"
            )
        await pending[0].commit()
        gateway.invoke_dingtalk_robot_outbox.assert_awaited_once()
        return run_id

    try:
        run_id = client.portal.call(dispatch)
        assert client.get(f"/api/v1/runs/{run_id}/artifacts").status_code == 404
    finally:
        if pending:
            client.portal.call(pending[0].close)


@pytest.mark.asyncio
async def test_connector_serialization_change_is_fenced_like_source_access_comparison(
    fence_sessions: async_sessionmaker[AsyncSession],
) -> None:
    from obsion.db.models import Connector

    organization, _ = await seed(fence_sessions)
    async with fence_sessions() as session, session.begin():
        connector = Connector(
            organization_id=organization,
            name="serialization-fence",
            connector_type="test",
            environment="test",
            status="ACTIVE",
            configuration={"a": 1, "b": 2},
        )
        session.add(connector)
        await session.flush()
        connector_id = connector.id
    async with fence_sessions() as publisher, fence_sessions() as writer:
        await acquire_source_publication_fence(publisher, organization)
        publisher_pid = await publisher.scalar(text("SELECT pg_backend_pid()"))
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))

        async def reorder():
            # Managed source access deliberately compares serialized JSON, so
            # equal JSONB values alone cannot determine a harmless update.
            await writer.execute(
                text("UPDATE connectors SET configuration = CAST(:config AS json) WHERE id=:id"),
                {"config": '{"b":2,"a":1}', "id": connector_id},
            )
            await writer.commit()

        pending = asyncio.create_task(reorder())
        try:
            await wait_blocked(fence_sessions, writer_pid, publisher_pid)
            assert not pending.done()
            await publisher.commit()
            await asyncio.wait_for(pending, 2)
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
