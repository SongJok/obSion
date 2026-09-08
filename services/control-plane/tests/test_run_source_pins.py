from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from obsion.application.run_source_pins import RunSourcePinService
from obsion.common.errors import AuthorizationError, ConflictError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import (
    AuditRecord,
    CodeRepository,
    CodeRepositoryGrant,
    Connector,
    Event,
    Organization,
    OutboxMessage,
    Policy,
    PolicyDecision,
    Role,
    Run,
    Thread,
    Turn,
    User,
    UserRole,
    Workspace,
)
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ProjectSource,
    ProjectSourceRevocation,
    RunSourcePin,
)
from obsion.db.session import Database
from obsion.domain.enums import Classification, ConnectorStatus, RunStatus, ThreadStatus, Visibility
from obsion.sandbox.project import ProjectFile, ProjectRevision, ProjectSnapshot
from obsion.security.identity import Principal


async def _pin(session, item, *, service=None, **changes):
    snapshot, raw_commit = _snapshot(
        item.organization.id,
        item.thread.workspace_id,
        item.repository.id,
        item.version.id,
    )
    return await (service or RunSourcePinService()).pin_verified_snapshot(
        session,
        item.principal,
        **{
            "run_id": item.run.id,
            "source_id": item.source.id,
            "snapshot": snapshot,
            "raw_commit": raw_commit,
            "environment": "test",
            **changes,
        },
    )


async def _fact_counts(session, org_id):
    counts = []
    for model in (RunSourcePin, Event, PolicyDecision, AuditRecord):
        counts.append(
            await session.scalar(
                select(func.count()).select_from(model).where(model.organization_id == org_id)
            )
        )
    return counts


@pytest.mark.parametrize("failure", ["audit", "event"])
async def test_pin_failure_rolls_back_all_facts_when_caller_commits(app_settings, failure):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            writer = SimpleNamespace(
                **{
                    ("write" if failure == "audit" else "append"): AsyncMock(
                        side_effect=RuntimeError("synthetic writer failure")
                    )
                }
            )
            service = RunSourcePinService(**{("audit" if failure == "audit" else "events"): writer})
            with pytest.raises(RuntimeError, match="synthetic writer failure"):
                await _pin(session, item, service=service)
            assert await _fact_counts(session, item.organization.id) == [0, 0, 0, 0]
            assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 0
            await session.refresh(item.run)
        async with database.sessions() as session, session.begin():
            assert await _fact_counts(session, item.organization.id) == [0, 0, 0, 0]
            created = await _pin(session, item)
            assert created.outcome == "CREATED"
            assert await _fact_counts(session, item.organization.id) == [1, 1, 1, 1]
            audit = await session.scalar(
                select(AuditRecord).where(AuditRecord.organization_id == item.organization.id)
            )
            decision = await session.get(PolicyDecision, audit.policy_decision_id)
            assert decision.action == "run.source.pin"
            assert decision.run_id == item.run.id
            assert decision.effect == "ALLOW"
    finally:
        await database.dispose()


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "CANCELLED"])
async def test_pin_rejects_current_terminal_status_despite_cached_run(app_settings, status):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            await session.execute(
                update(Run).where(Run.id == item.run.id).values(status=status),
                execution_options={"synchronize_session": False},
            )
            assert item.run.status == RunStatus.PENDING
            with pytest.raises(ConflictError):
                await _pin(session, item)
            assert await _fact_counts(session, item.organization.id) == [0, 0, 0, 0]
    finally:
        await database.dispose()


@pytest.mark.parametrize("environment", ["production", "TEST", "", None, []])
async def test_pin_rejects_unsupported_environment(app_settings, environment):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            with pytest.raises(ValidationError):
                await _pin(session, item, environment=environment)
            assert await _fact_counts(session, item.organization.id) == [0, 0, 0, 0]
    finally:
        await database.dispose()


async def test_existing_pin_cannot_be_replaced_by_another_valid_commit(app_settings):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            original = await _pin(session, item)
            snapshot, raw_commit = _snapshot(
                item.organization.id, item.thread.workspace_id, item.repository.id, item.version.id
            )
            changed_commit = raw_commit + b"another commit message\n"
            changed_snapshot = replace(
                snapshot,
                revision=replace(
                    snapshot.revision, commit_id=_object_id(b"commit", changed_commit)
                ),
            )
            with pytest.raises(ConflictError):
                await _pin(session, item, snapshot=changed_snapshot, raw_commit=changed_commit)
            assert await _fact_counts(session, item.organization.id) == [1, 1, 1, 1]
            assert original.pin.commit_id == snapshot.revision.commit_id
    finally:
        await database.dispose()


@pytest.mark.parametrize("change", ["raw_commit", "organization", "workspace", "source"])
async def test_unverified_or_foreign_snapshot_cannot_create_facts(app_settings, change):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            snapshot, raw_commit = _snapshot(
                item.organization.id, item.thread.workspace_id, item.repository.id, item.version.id
            )
            options = {}
            if change == "raw_commit":
                raw_commit += b"corrupted\n"
            elif change == "source":
                options["source_id"] = uuid4()
            else:
                snapshot = replace(
                    snapshot,
                    revision=replace(snapshot.revision, **{f"{change}_id": uuid4()}),
                )
            with pytest.raises(ValidationError if change == "raw_commit" else AuthorizationError):
                await _pin(session, item, snapshot=snapshot, raw_commit=raw_commit, **options)
            assert await _fact_counts(session, item.organization.id) == [0, 0, 0, 0]
    finally:
        await database.dispose()


async def test_pin_requires_caller_transaction(app_settings):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
        async with database.sessions() as session:
            with pytest.raises(ValidationError):
                await _pin(session, item)
            assert not session.in_transaction()
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "change",
    ["deny", "ask", "mask", "obligation", "disabled_policy", "revoked_role", "inactive_user"],
)
async def test_pin_rechecks_current_policy_and_identity(app_settings, change):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            snapshot, raw_commit = _snapshot(
                item.organization.id,
                item.thread.workspace_id,
                item.repository.id,
                item.version.id,
            )
            if change in {"deny", "ask", "mask"}:
                item.policy.effect = change.upper()
            elif change == "obligation":
                item.policy.obligations = [{"type": "manual_review"}]
            elif change == "disabled_policy":
                item.policy.enabled = False
            elif change == "revoked_role":
                item.role.permissions = []
            else:
                item.user.active = False
            await session.flush()
            events = SimpleNamespace(append=AsyncMock())
            audit = SimpleNamespace(write=AsyncMock())
            service = RunSourcePinService(events=events, audit=audit)
            with pytest.raises(AuthorizationError):
                await service.pin_verified_snapshot(
                    session,
                    item.principal,
                    run_id=item.run.id,
                    source_id=item.source.id,
                    snapshot=snapshot,
                    raw_commit=raw_commit,
                    environment="test",
                )
            assert await session.scalar(select(RunSourcePin.id)) is None
            events.append.assert_not_awaited()
    finally:
        await database.dispose()


def _object_id(kind: bytes, content: bytes) -> str:
    return hashlib.sha1(
        kind + b" " + str(len(content)).encode() + b"\0" + content,
        usedforsecurity=False,
    ).hexdigest()


def _snapshot(org_id, workspace_id, repository_id, version_id):
    file = ProjectFile("main.py", b"print('ok')\n")
    blob = _object_id(b"blob", file.content)
    tree_raw = b"100644 main.py\0" + bytes.fromhex(blob)
    tree_id = _object_id(b"tree", tree_raw)
    raw_commit = (
        b"tree " + tree_id.encode() + b"\nauthor Synthetic <synthetic@example.invalid> 0 +0000\n"
        b"committer Synthetic <synthetic@example.invalid> 0 +0000\n\nsource\n"
    )
    commit_id = _object_id(b"commit", raw_commit)
    snapshot = ProjectSnapshot(
        ProjectRevision(
            org_id,
            workspace_id,
            repository_id,
            version_id,
            commit_id,
            tree_id,
        ),
        (file,),
    )
    return snapshot, raw_commit


async def _setup(session):
    org_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    repository_id = uuid4()
    connector_id = uuid4()
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    version_id = uuid4()
    source_id = uuid4()
    org = Organization(id=org_id, slug=f"org-{uuid4().hex[:8]}", name="Synthetic")
    user = User(
        id=user_id,
        organization_id=org.id,
        external_id=f"user-{uuid4().hex}",
        email="source@example.invalid",
        display_name="Source User",
    )
    workspace = Workspace(
        id=workspace_id,
        organization_id=org.id,
        owner_id=user.id,
        name="Source Workspace",
        classification=Classification.INTERNAL,
        visibility=Visibility.PRIVATE,
    )
    repository = CodeRepository(
        id=repository_id,
        organization_id=org.id,
        name="synthetic/repository",
        classification=Classification.INTERNAL,
    )
    connector = Connector(
        id=connector_id,
        organization_id=org.id,
        name="source-connector",
        connector_type="git-http",
        status=ConnectorStatus.ACTIVE,
        environment="test",
        endpoint="https://source.example.invalid",
        configuration={"allowed_repositories": ["synthetic/repository"]},
        declared_grants=["code.read"],
        allowed_egress=["source.example.invalid"],
    )
    thread = Thread(
        id=thread_id,
        organization_id=org.id,
        workspace_id=workspace.id,
        title="Source pin",
        status=ThreadStatus.ACTIVE,
        created_by=user.id,
    )
    turn = Turn(
        id=turn_id,
        organization_id=org.id,
        thread_id=thread.id,
        ordinal=1,
        created_by=user.id,
        input_text="inspect",
        sanitized_input="inspect",
        created_at=utc_now(),
    )
    run = Run(
        id=run_id,
        organization_id=org.id,
        turn_id=turn.id,
        status=RunStatus.PENDING,
        max_steps=10,
        timeout_seconds=120,
        max_input_tokens=1000,
        max_output_tokens=1000,
        max_cost_amount=1,
    )
    # Real PostgreSQL enforces these FKs immediately; fixtures must honor the
    # same aggregate creation order as the application services.
    for records in ([org], [user], [workspace, repository, connector], [thread], [turn], [run]):
        session.add_all(records)
        await session.flush()
    role = Role(
        organization_id=org.id,
        name="source-pinner",
        permissions=["run.source.pin", "code.read.internal"],
    )
    policy = Policy(
        organization_id=org.id,
        name="source-pin-allow",
        version=1,
        effect="ALLOW",
        conditions={"actions": ["run.source.pin"], "context": {"environment": "test"}},
        reason="Synthetic source pin authorization",
        created_by=user.id,
    )
    session.add_all([role, policy])
    await session.flush()
    session.add(UserRole(organization_id=org.id, user_id=user.id, role_id=role.id))
    version = ConnectorConfigurationVersion(
        id=version_id,
        organization_id=org.id,
        connector_id=connector.id,
        connector_type=connector.connector_type,
        environment=connector.environment,
        endpoint=connector.endpoint,
        configuration=connector.configuration,
        credential_ref=connector.credential_ref,
        declared_grants=connector.declared_grants,
        allowed_egress=connector.allowed_egress,
        created_by=user.id,
    )
    session.add(version)
    await session.flush()
    source = ProjectSource(
        id=source_id,
        organization_id=org.id,
        workspace_id=workspace.id,
        repository_id=repository.id,
        connector_version_id=version.id,
        provider_repository_id="synthetic/repository",
        created_by=user.id,
    )
    session.add(source)
    await session.flush()
    principal = Principal(
        user.id,
        org.id,
        user.external_id,
        user.display_name,
        permissions=frozenset({"run.source.pin", "code.read.internal"}),
    )
    return SimpleNamespace(
        principal=principal,
        run=run,
        source=source,
        version=version,
        organization=org,
        repository=repository,
        role=role,
        policy=policy,
        user=user,
        thread=thread,
        connector=connector,
    )


@pytest.mark.asyncio
async def test_verified_snapshot_is_pinned_idempotently_and_audited(app_settings):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            turn = await session.scalar(select(Turn).where(Turn.id == item.run.turn_id))
            thread = await session.scalar(select(Thread).where(Thread.id == turn.thread_id))
            snapshot, raw_commit = _snapshot(
                item.organization.id, thread.workspace_id, item.repository.id, item.version.id
            )
            events = SimpleNamespace(append=AsyncMock())
            audit = SimpleNamespace(write=AsyncMock())
            service = RunSourcePinService(events=events, audit=audit)
            result = await service.pin_verified_snapshot(
                session,
                item.principal,
                run_id=item.run.id,
                source_id=item.source.id,
                snapshot=snapshot,
                raw_commit=raw_commit,
                environment="test",
            )
            replay = await service.pin_verified_snapshot(
                session,
                item.principal,
                run_id=item.run.id,
                source_id=item.source.id,
                snapshot=snapshot,
                raw_commit=raw_commit,
                environment="test",
            )
            assert result.outcome == "CREATED"
            assert replay.outcome == "UNCHANGED"
            assert replay.pin.id == result.pin.id
            assert await session.scalar(
                select(RunSourcePin).where(RunSourcePin.run_id == item.run.id)
            )
            events.append.assert_awaited_once()
            audit.write.assert_awaited_once()
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_source_revocation_blocks_a_new_pin_and_never_writes(app_settings):
    database = Database(app_settings)
    try:
        async with database.sessions() as session, session.begin():
            item = await _setup(session)
            turn = await session.scalar(select(Turn).where(Turn.id == item.run.turn_id))
            thread = await session.scalar(select(Thread).where(Thread.id == turn.thread_id))
            snapshot, raw_commit = _snapshot(
                item.organization.id, thread.workspace_id, item.repository.id, item.version.id
            )
            session.add(
                ProjectSourceRevocation(
                    organization_id=item.organization.id,
                    source_id=item.source.id,
                    revoked_by=item.principal.id,
                    reason_code="SECURITY_REVOKED",
                )
            )
            await session.flush()
            service = RunSourcePinService(
                events=SimpleNamespace(append=AsyncMock()),
                audit=SimpleNamespace(write=AsyncMock()),
            )
            with pytest.raises(AuthorizationError) as caught:
                await service.pin_verified_snapshot(
                    session,
                    item.principal,
                    run_id=item.run.id,
                    source_id=item.source.id,
                    snapshot=snapshot,
                    raw_commit=raw_commit,
                    environment="test",
                )
            assert getattr(caught.value, "code", None) == "project_source_denied"
            assert (
                await session.scalar(select(RunSourcePin).where(RunSourcePin.run_id == item.run.id))
                is None
            )
    finally:
        await database.dispose()


@pytest.mark.parametrize("revocation", ["source", "repository"])
def test_source_pin_inspection_rechecks_revocation_and_hides_configuration(
    client, app_settings, revocation
) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Source pin inspection", "description": "Synthetic pin inspection"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Source pin history"},
    )
    assert thread.status_code == 201, thread.text
    turn = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "Inspect a verified source revision."},
    )
    assert turn.status_code == 202, turn.text
    run_id = UUID(turn.json()["run"]["id"])
    workspace_id = UUID(workspace.json()["id"])

    async def seed_pin() -> SimpleNamespace:
        database = Database(app_settings)
        try:
            async with database.sessions() as session, session.begin():
                repository = CodeRepository(
                    organization_id=app_settings.dev_organization_id,
                    name=f"source-pin/{uuid4().hex}",
                    classification=Classification.INTERNAL,
                )
                connector = Connector(
                    organization_id=app_settings.dev_organization_id,
                    name=f"source-pin-{uuid4().hex}",
                    connector_type="git-http",
                    status=ConnectorStatus.ACTIVE,
                    environment="test",
                    endpoint="https://source.example.invalid",
                    configuration={"allowed_repositories": [repository.name]},
                    declared_grants=["code.read"],
                    allowed_egress=["source.example.invalid"],
                )
                session.add_all([repository, connector])
                await session.flush()
                version = ConnectorConfigurationVersion(
                    organization_id=app_settings.dev_organization_id,
                    connector_id=connector.id,
                    connector_type=connector.connector_type,
                    environment=connector.environment,
                    endpoint=connector.endpoint,
                    configuration=connector.configuration,
                    credential_ref=connector.credential_ref,
                    declared_grants=connector.declared_grants,
                    allowed_egress=connector.allowed_egress,
                    created_by=app_settings.dev_user_id,
                )
                session.add(version)
                await session.flush()
                source = ProjectSource(
                    organization_id=app_settings.dev_organization_id,
                    workspace_id=workspace_id,
                    repository_id=repository.id,
                    connector_version_id=version.id,
                    provider_repository_id="synthetic/source-pin",
                    created_by=app_settings.dev_user_id,
                )
                session.add(source)
                await session.flush()
                snapshot, _ = _snapshot(
                    app_settings.dev_organization_id,
                    workspace_id,
                    repository.id,
                    version.id,
                )
                pin = RunSourcePin(
                    organization_id=app_settings.dev_organization_id,
                    run_id=run_id,
                    source_id=source.id,
                    connector_version_id=version.id,
                    workspace_id=workspace_id,
                    repository_id=repository.id,
                    commit_id=snapshot.revision.commit_id,
                    tree_id=snapshot.revision.tree_id,
                    snapshot_fingerprint=snapshot.fingerprint,
                    file_count=len(snapshot.files),
                    snapshot_bytes=sum(len(file.content) for file in snapshot.files),
                    pinned_by=app_settings.dev_user_id,
                )
                session.add(pin)
                await session.flush()
                return SimpleNamespace(pin=pin, source=source)
        finally:
            await database.dispose()

    item = asyncio.run(seed_pin())
    response = client.get(f"/api/v1/runs/{run_id}/source-pins")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    view = response.json()[0]
    assert view["id"] == str(item.pin.id)
    assert view["source_id"] == str(item.source.id)
    assert view["commit_id"] == item.pin.commit_id
    assert view["source_available"] is True
    for forbidden in ("configuration", "credential_ref", "endpoint", "provider_repository_id"):
        assert forbidden not in view

    async def deny_repository() -> None:
        database = Database(app_settings)
        try:
            async with database.sessions() as session, session.begin():
                session.add(
                    CodeRepositoryGrant(
                        organization_id=app_settings.dev_organization_id,
                        repository_id=item.pin.repository_id,
                        subject_type="USER",
                        subject_value=str(app_settings.dev_user_id),
                        effect="DENY",
                        created_at=utc_now(),
                    )
                )
        finally:
            await database.dispose()

    async def revoke_source() -> None:
        database = Database(app_settings)
        try:
            async with database.sessions() as session, session.begin():
                session.add(
                    ProjectSourceRevocation(
                        organization_id=app_settings.dev_organization_id,
                        source_id=item.source.id,
                        revoked_by=app_settings.dev_user_id,
                        reason_code="SECURITY_REVOKED",
                    )
                )
        finally:
            await database.dispose()

    asyncio.run(revoke_source() if revocation == "source" else deny_repository())
    revoked = client.get(f"/api/v1/runs/{run_id}/source-pins")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()[0]["id"] == str(item.pin.id)
    assert revoked.json()[0]["source_available"] is False

    missing = client.get(f"/api/v1/runs/{uuid4()}/source-pins")
    assert missing.status_code == 404
