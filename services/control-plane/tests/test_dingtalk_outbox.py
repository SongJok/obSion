from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from obsion.application.dingtalk_outbox import (
    DingTalkOutboxService,
    OutboxClaim,
    OutboxReconciliationClaim,
    _group_delivery,
    _millis_to_datetime,
)
from obsion.application.dingtalk_outbox_worker import DingTalkOutboxWorker
from obsion.capabilities.dingtalk_robot import RobotQueryResult, RobotQueryState
from obsion.capabilities.gateway import DingTalkRobotOutboxQueryResult
from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.im_models import (
    ImGroupAudience,
    ImInboxMessage,
    ImInstallation,
    ImInstallationBinding,
)
from obsion.db.models import (
    Connector,
    DingTalkRobotOutbox,
    Organization,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
    WorkspaceMember,
)
from obsion.db.session import Database
from obsion.domain.enums import DingTalkOutboxStatus, RunStatus
from obsion.security.identity import Principal


class _NoopAudit:
    async def write(self, session, draft):  # type: ignore[no-untyped-def]
        del session, draft
        return None


class _NoopGateway:
    pass


async def _database(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}",
        dev_bearer_token="outbox-test-bearer-token",
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def _group_context(
    tmp_path,
    *,
    max_classification: str = "INTERNAL",
    allow_final_answer: bool = False,
    allow_status: bool = True,
) -> tuple[Database, Principal, UUID, UUID, UUID]:  # type: ignore[no-untyped-def]
    database = await _database(tmp_path)
    now = utc_now()
    async with database.sessions() as session, session.begin():
        organization = Organization(slug=f"group-{uuid4().hex}", name="Group tests")
        session.add(organization)
        await session.flush()
        owner = User(
            organization_id=organization.id,
            external_id="group-owner",
            email="group-owner@example.test",
            display_name="Group owner",
        )
        member = User(
            organization_id=organization.id,
            external_id="group-member",
            email="group-member@example.test",
            display_name="Group member",
        )
        session.add_all([owner, member])
        await session.flush()
        connector = Connector(
            organization_id=organization.id,
            name="group-connector",
            connector_type="dingtalk-docs",
            environment="test",
        )
        workspace = Workspace(
            organization_id=organization.id,
            name="Group workspace",
            owner_id=owner.id,
        )
        session.add_all([connector, workspace])
        await session.flush()
        session.add(
            WorkspaceMember(
                organization_id=organization.id,
                workspace_id=workspace.id,
                user_id=member.id,
                permissions=["read", "write"],
                can_write=True,
                created_by=owner.id,
                created_at=now,
            )
        )
        installation = ImInstallation(
            organization_id=organization.id,
            provider="dingtalk",
            external_corp_id="group-corp",
            external_app_id="group-app",
            connector_id=connector.id,
            adapter_principal_id=owner.id,
            status="ACTIVE",
            created_by=owner.id,
            verification_source="test",
        )
        session.add(installation)
        await session.flush()
        binding = ImInstallationBinding(
            organization_id=organization.id,
            installation_id=installation.id,
            sender_id="group-sender",
            user_id=member.id,
            active=True,
            created_by=owner.id,
        )
        session.add(binding)
        thread = Thread(
            organization_id=organization.id,
            workspace_id=workspace.id,
            title="Group task",
            created_by=member.id,
        )
        session.add(thread)
        await session.flush()
        turn = Turn(
            organization_id=organization.id,
            thread_id=thread.id,
            ordinal=1,
            created_by=member.id,
            input_text="group task",
            sanitized_input="group task",
            created_at=now,
        )
        session.add(turn)
        await session.flush()
        run = Run(
            organization_id=organization.id,
            turn_id=turn.id,
            status=RunStatus.COMPLETED,
            completed_at=now,
        )
        session.add(run)
        await session.flush()
        inbox = ImInboxMessage(
            organization_id=organization.id,
            installation_id=installation.id,
            vendor_event_id="group-event",
            fingerprint="a" * 64,
            binding_id=binding.id,
            subject_user_id=member.id,
            sender_id="group-sender",
            conversation_id="open-group",
            conversation_type="group",
            text="group task",
            status="PROCESSED",
            turn_id=turn.id,
            run_id=run.id,
            processed_at=now,
        )
        audience = ImGroupAudience(
            organization_id=organization.id,
            installation_id=installation.id,
            conversation_id="open-group",
            workspace_id=workspace.id,
            member_user_ids=[str(member.id)],
            member_fingerprint="b" * 64,
            max_classification=max_classification,
            allow_final_answer=allow_final_answer,
            allow_status=allow_status,
            status="ACTIVE",
            created_by=owner.id,
            verification_source="operator-test",
            verified_at=now,
            verified_until=now + timedelta(minutes=5),
        )
        session.add_all([inbox, audience])
        await session.flush()
        principal = Principal(
            id=member.id,
            organization_id=organization.id,
            external_id=member.external_id,
            display_name=member.display_name,
            permissions=frozenset({"member"}),
        )
        return database, principal, inbox.id, run.id, audience.id


@pytest.mark.asyncio
async def test_group_outbox_policy_allows_authorized_final_answer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database, principal, inbox_id, run_id, _audience_id = await _group_context(
        tmp_path, max_classification="INTERNAL", allow_final_answer=True
    )
    async with database.sessions() as session, session.begin():
        inbox = await session.get(ImInboxMessage, inbox_id)
        run = await session.get(Run, run_id)
        assert inbox is not None and run is not None
        result = await _group_delivery(
            session, run, inbox, principal, "authorized answer", "INTERNAL"
        )
        assert result is not None
        audience, mode, text = result
        assert audience.id == _audience_id
        assert mode == "FINAL"
        assert text == "authorized answer"
    await database.dispose()


@pytest.mark.asyncio
async def test_group_outbox_downgrades_sensitive_answer_to_fixed_status(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database, principal, inbox_id, run_id, _audience_id = await _group_context(
        tmp_path, max_classification="INTERNAL", allow_final_answer=False, allow_status=True
    )
    async with database.sessions() as session, session.begin():
        inbox = await session.get(ImInboxMessage, inbox_id)
        run = await session.get(Run, run_id)
        assert inbox is not None and run is not None
        result = await _group_delivery(
            session, run, inbox, principal, "RESTRICTED answer must not leak", "RESTRICTED"
        )
        assert result is not None
        _audience, mode, text = result
        assert mode == "STATUS"
        assert text == "任务已完成，结果未在群内公开。请在 Obsion 工作台登录查看。"
        assert "RESTRICTED" not in text
    await database.dispose()


@pytest.mark.asyncio
async def test_group_outbox_fails_closed_after_audience_expiry_or_acl_revoke(tmp_path) -> None:  # type: ignore[no-untyped-def]
    database, principal, inbox_id, run_id, audience_id = await _group_context(
        tmp_path, max_classification="RESTRICTED", allow_final_answer=True
    )
    async with database.sessions() as session, session.begin():
        audience = await session.get(ImGroupAudience, audience_id)
        inbox = await session.get(ImInboxMessage, inbox_id)
        run = await session.get(Run, run_id)
        assert audience is not None and inbox is not None and run is not None
        audience.verified_until = utc_now() - timedelta(seconds=1)
        await session.flush()
        assert await _group_delivery(session, run, inbox, principal, "answer", "INTERNAL") is None
    async with database.sessions() as session, session.begin():
        audience = await session.get(ImGroupAudience, audience_id)
        inbox = await session.get(ImInboxMessage, inbox_id)
        run = await session.get(Run, run_id)
        assert audience is not None and inbox is not None and run is not None
        audience.verified_until = utc_now() + timedelta(minutes=5)
        member = await session.scalar(
            select(WorkspaceMember).where(WorkspaceMember.user_id == principal.id)
        )
        assert member is not None
        await session.delete(member)
        await session.flush()
        assert await _group_delivery(session, run, inbox, principal, "answer", "INTERNAL") is None
    await database.dispose()


def _item() -> DingTalkRobotOutbox:
    return DingTalkRobotOutbox(
        organization_id=uuid4(),
        installation_id=uuid4(),
        inbox_message_id=uuid4(),
        binding_id=uuid4(),
        run_id=uuid4(),
        recipient_user_id=uuid4(),
        recipient_sender_id="sender-1",
        connector_id=uuid4(),
        connector_fingerprint="a" * 64,
        content_fingerprint="b" * 64,
        status=DingTalkOutboxStatus.QUEUED,
        attempt_count=0,
        fencing_token=0,
        next_attempt_at=utc_now(),
    )


def _accepted_item() -> DingTalkRobotOutbox:
    item = _item()
    item.status = DingTalkOutboxStatus.ACCEPTED
    item.capability_version_id = uuid4()
    item.accepted_process_query_key = "process-query-key"
    item.next_attempt_at = None
    return item


def _reconciliation_inputs(item: DingTalkRobotOutbox):
    installation = SimpleNamespace(id=item.installation_id, external_app_id="app-key")
    connector = SimpleNamespace(
        id=item.connector_id,
        connector_type="dingtalk-robot",
        environment="test",
        endpoint="https://api.dingtalk.com",
        configuration={
            "protocol": "dingtalk.robot.oto.v1",
            "app_key": "app-key",
            "robot_code": "robot-code",
        },
        credential_ref="OBSION_TEST_SECRET",
        declared_grants=["im.reply.deliver"],
        allowed_egress=["api.dingtalk.com"],
    )
    principal = Principal(
        id=item.recipient_user_id,
        organization_id=item.organization_id,
        external_id="user-1",
        display_name="User 1",
        permissions=frozenset({"admin.read"}),
    )
    return installation, connector, item.capability_version_id, principal


class _QueryGateway:
    def __init__(self, query: RobotQueryResult) -> None:
        self.query = query
        self.calls = 0

    async def reconcile_dingtalk_robot_outbox(self, session, request, *, transport=None):
        del session, request, transport
        self.calls += 1
        return DingTalkRobotOutboxQueryResult(
            self.query,
            uuid4(),
            uuid4(),
            uuid4(),
        )


class _WorkerTransaction:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        self.events.append("begin")
        return self

    async def __aexit__(self, exc_type, exc, traceback):  # type: ignore[no-untyped-def]
        self.events.append("rollback" if exc_type else "commit")


class _WorkerSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def begin(self) -> _WorkerTransaction:
        return _WorkerTransaction(self.events)


class _WorkerSessions:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.session = _WorkerSession(events)

    async def __aenter__(self) -> _WorkerSession:
        self.events.append("open")
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        self.events.append("close")


class _WorkerDatabase:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def sessions(self) -> _WorkerSessions:
        return _WorkerSessions(self.events)


class _WorkerService:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def expire_claims(self, session):  # type: ignore[no-untyped-def]
        del session
        self.events.append("expire")
        return 2

    async def enqueue_next(self, session):  # type: ignore[no-untyped-def]
        del session
        self.events.append("enqueue")
        return object()

    async def claim_next(self, session):  # type: ignore[no-untyped-def]
        del session
        self.events.append("claim_send")
        return OutboxClaim(uuid4(), 1)

    async def dispatch(self, session, claim):  # type: ignore[no-untyped-def]
        del session, claim
        self.events.append("dispatch")
        return SimpleNamespace(status=DingTalkOutboxStatus.BLOCKED)

    async def claim_reconciliation(self, session):  # type: ignore[no-untyped-def]
        del session
        self.events.append("claim_reconcile")
        return OutboxReconciliationClaim(uuid4(), 1)

    async def reconcile(self, session, claim):  # type: ignore[no-untyped-def]
        del session, claim
        self.events.append("reconcile")
        return object()


@pytest.mark.asyncio
async def test_claim_is_single_use_and_fencing_rejects_stale_dispatch(tmp_path) -> None:
    database = await _database(tmp_path)
    service = DingTalkOutboxService(_NoopGateway(), lease_seconds=30, max_attempts=3)
    service.audit = _NoopAudit()  # type: ignore[assignment]
    item = _item()
    async with database.sessions() as session, session.begin():
        session.add(item)
    async with database.sessions() as session, session.begin():
        claim = await service.claim_next(session)
    assert claim is not None
    assert claim.fencing_token == 1
    async with database.sessions() as session, session.begin():
        assert await service.claim_next(session) is None
        current = await session.scalar(
            select(DingTalkRobotOutbox).where(DingTalkRobotOutbox.id == item.id)
        )
        assert current is not None
        assert current.status == DingTalkOutboxStatus.DISPATCHING
        assert current.attempt_count == 1
    async with database.sessions() as session, session.begin():
        assert (
            await service.dispatch(session, type(claim)(claim.outbox_id, claim.fencing_token - 1))
            is None
        )
    await database.dispose()


@pytest.mark.asyncio
async def test_expired_claim_becomes_unknown_without_requeue(tmp_path) -> None:
    database = await _database(tmp_path)
    service = DingTalkOutboxService(_NoopGateway(), lease_seconds=30, max_attempts=3)
    service.audit = _NoopAudit()  # type: ignore[assignment]
    item = _item()
    async with database.sessions() as session, session.begin():
        session.add(item)
    async with database.sessions() as session, session.begin():
        claim = await service.claim_next(session)
    assert claim is not None
    async with database.sessions() as session, session.begin():
        current = await session.scalar(
            select(DingTalkRobotOutbox).where(DingTalkRobotOutbox.id == item.id)
        )
        assert current is not None
        current.lease_expires_at = utc_now() - timedelta(seconds=1)
    async with database.sessions() as session, session.begin():
        assert await service.expire_claims(session) == 1
    async with database.sessions() as session, session.begin():
        current = await session.scalar(
            select(DingTalkRobotOutbox).where(DingTalkRobotOutbox.id == item.id)
        )
        assert current is not None
        assert current.status == DingTalkOutboxStatus.UNKNOWN
        assert current.last_error_code == "im_delivery_receipt_conflict"
        assert current.next_attempt_at is None
        assert await service.claim_next(session) is None
    await database.dispose()


@pytest.mark.asyncio
async def test_reconciliation_claim_is_committed_and_stale_claim_is_rejected(tmp_path) -> None:
    database = await _database(tmp_path)
    service = DingTalkOutboxService(_NoopGateway(), lease_seconds=30, max_attempts=3)
    service.audit = _NoopAudit()  # type: ignore[assignment]
    item = _accepted_item()
    async with database.sessions() as session, session.begin():
        session.add(item)
    async with database.sessions() as session, session.begin():
        claim = await service.claim_reconciliation(session)
    assert claim is not None
    async with database.sessions() as session, session.begin():
        current = await session.scalar(
            select(DingTalkRobotOutbox).where(DingTalkRobotOutbox.id == item.id)
        )
        assert current is not None
        assert current.reconciliation_attempt_count == 1
        assert current.last_reconciled_at is not None
        assert (
            await service.reconcile(session, type(claim)(claim.outbox_id, claim.attempt_count - 1))
            is None
        )
    await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "status", "last_error"),
    [
        (
            RobotQueryResult(
                RobotQueryState.SUCCESS,
                send_status="SUCCESS",
                read_status="READ",
                read_timestamp_ms=1_700_000_000_000,
            ),
            DingTalkOutboxStatus.ACCEPTED,
            None,
        ),
        (
            RobotQueryResult(RobotQueryState.FAILURE, send_status="FAILED"),
            DingTalkOutboxStatus.REJECTED,
            "dependency_failed",
        ),
        (
            RobotQueryResult(
                RobotQueryState.UNKNOWN,
                send_status="PENDING",
                reason="reconciliation_status_unknown",
            ),
            DingTalkOutboxStatus.ACCEPTED,
            "im_delivery_receipt_conflict",
        ),
    ],
)
async def test_reconciliation_persists_vendor_state_without_resending(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    query: RobotQueryResult,
    status: DingTalkOutboxStatus,
    last_error: str | None,
) -> None:
    database = await _database(tmp_path)
    gateway = _QueryGateway(query)
    service = DingTalkOutboxService(gateway, lease_seconds=30, max_attempts=3)
    service.audit = _NoopAudit()  # type: ignore[assignment]
    item = _accepted_item()
    async with database.sessions() as session, session.begin():
        session.add(item)

    async def valid_inputs(session, outbox):  # type: ignore[no-untyped-def]
        del session
        return _reconciliation_inputs(outbox)

    monkeypatch.setattr(service, "_reconciliation_inputs", valid_inputs)
    async with database.sessions() as session, session.begin():
        claim = await service.claim_reconciliation(session)
    assert claim is not None
    async with database.sessions() as session, session.begin():
        result = await service.reconcile(session, claim)
        assert result is not None
        assert result.status == status
        assert result.last_error_code == last_error
        assert result.vendor_send_status == query.send_status
        assert result.vendor_read_status == query.read_status
        assert gateway.calls == 1
        if query.read_timestamp_ms is not None:
            assert result.vendor_read_at == datetime.fromtimestamp(
                query.read_timestamp_ms / 1000, tz=UTC
            )
    await database.dispose()


@pytest.mark.asyncio
async def test_reconciliation_precondition_change_fails_closed(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    database = await _database(tmp_path)
    service = DingTalkOutboxService(_NoopGateway(), lease_seconds=30, max_attempts=3)
    service.audit = _NoopAudit()  # type: ignore[assignment]
    item = _accepted_item()
    async with database.sessions() as session, session.begin():
        session.add(item)

    async def missing_inputs(session, outbox):  # type: ignore[no-untyped-def]
        del session, outbox
        return None

    monkeypatch.setattr(service, "_reconciliation_inputs", missing_inputs)
    async with database.sessions() as session, session.begin():
        claim = await service.claim_reconciliation(session)
    assert claim is not None
    async with database.sessions() as session, session.begin():
        result = await service.reconcile(session, claim)
        assert result is not None
        assert result.status == DingTalkOutboxStatus.ACCEPTED
        assert result.last_error_code == "im_delivery_lineage_changed"
    await database.dispose()


def test_reconciliation_timestamp_parser_fails_closed_for_bad_epoch() -> None:
    assert _millis_to_datetime(None) is None
    assert _millis_to_datetime(-1) is None
    assert _millis_to_datetime(10**30) is None


@pytest.mark.asyncio
async def test_worker_commits_reconciliation_claim_before_read_only_query() -> None:
    events: list[str] = []
    worker = DingTalkOutboxWorker(
        _WorkerDatabase(events),  # type: ignore[arg-type]
        _WorkerService(events),  # type: ignore[arg-type]
        poll_interval_seconds=1,
    )
    assert await worker.run_once() == {
        "queued": 1,
        "dispatched": 1,
        "reconciled": 1,
        "unknown": 2,
        "blocked": 1,
    }
    assert events.index("claim_reconcile") < events.index("reconcile")
    assert events[events.index("claim_reconcile") + 1 : events.index("reconcile")] == [
        "commit",
        "close",
        "open",
        "begin",
    ]
