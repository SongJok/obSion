"""纯合成管理登记；普通 SQLite 回归不冒充 PostgreSQL 并发与行锁证据。"""

import asyncio
import json
import os
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from obsion.application.project_sources import ProjectSourceService
from obsion.capabilities.codeup_contract import CODEUP_ORIGIN
from obsion.common.time import utc_now
from obsion.db.base import Base
from obsion.db.models import (
    AuditRecord,
    CodeRepository,
    CodeRepositoryGrant,
    Connector,
    Organization,
    Policy,
    PolicyDecision,
    Role,
    SecretReference,
    User,
    UserRole,
    Workspace,
    WorkspaceMember,
)
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
)
from obsion.sandbox.project import ProjectRejected, ProjectRevision
from obsion.sandbox.source_state import check_project_source_state
from obsion.security.identity import Principal

_ACTIONS = [
    "project_source.version.create",
    "project_source.register",
    "project_source.version.revoke",
    "project_source.revoke",
]
_FACTS = (
    ConnectorConfigurationVersion,
    ProjectSource,
    ConnectorVersionRevocation,
    ProjectSourceRevocation,
)
_SERVICE = ProjectSourceService(environment="test")


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def session(request, tmp_path):
    if request.param == "postgresql":
        if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
            pytest.skip("PostgreSQL source management requires explicit opt-in")
        url = os.getenv("OBSION_DATABASE_URL", "")
        assert url.startswith("postgresql+asyncpg://")
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'source-management.db'}"
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            if request.param == "sqlite":
                await connection.execute(text("PRAGMA foreign_keys=ON"))
                await connection.run_sync(Base.metadata.create_all)
                await connection.commit()
            transaction = await connection.begin()
            try:
                async with async_sessionmaker(bind=connection, expire_on_commit=False)() as value:
                    yield value
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@dataclass
class Setup:
    actor: Principal
    connector: Connector
    workspace: Workspace
    repository: CodeRepository
    role: Role
    policy: Policy


async def setup(session):
    org = Organization(slug=f"source-management-{uuid4()}", name="合成组织")
    session.add(org)
    await session.flush()
    user = User(
        organization_id=org.id,
        external_id="operator",
        display_name="合成操作方",
        email="operator@example.invalid",
    )
    role = Role(
        organization_id=org.id,
        name="source-operator",
        permissions=[*_ACTIONS, "code.read.internal"],
    )
    session.add_all([user, role])
    await session.flush()
    session.add(UserRole(organization_id=org.id, user_id=user.id, role_id=role.id))
    secret = SecretReference(
        organization_id=org.id,
        name="fixture-reference",
        provider="env",
        external_ref="env://UNRESOLVED_SYNTHETIC_SOURCE",
    )
    connector = Connector(
        organization_id=org.id,
        name="fixture",
        connector_type="git-http",
        status="ACTIVE",
        environment="test",
        endpoint="https://source.example.invalid",
        configuration={"allowed_repositories": ["synthetic/42", "synthetic/43"]},
        declared_grants=["code.read"],
        allowed_egress=["source.example.invalid"],
        credential_ref="secret://fixture-reference",
    )
    workspace = Workspace(
        organization_id=org.id, owner_id=user.id, name="合成项目", visibility="PRIVATE"
    )
    repository = CodeRepository(
        organization_id=org.id, name="合成仓库", classification="INTERNAL", acl={}
    )
    policy = Policy(
        organization_id=org.id,
        name="synthetic-source-allow",
        version=1,
        effect="ALLOW",
        conditions={"actions": _ACTIONS, "context": {"environment": "test"}},
        obligations=[],
        reason="仅合成测试",
        created_by=user.id,
    )
    session.add_all([secret, connector, workspace, repository, policy])
    await session.flush()
    return Setup(
        Principal(user.id, org.id, "operator", "合成操作方"),
        connector,
        workspace,
        repository,
        role,
        policy,
    )


async def version(session, item):
    result = await _SERVICE.create_connector_version(
        session, item.actor, connector_id=item.connector.id
    )
    assert result.outcome == "CREATED"
    assert result.record_id is not None
    return result.record_id


async def register(session, item, version_id, **changes):
    kwargs = dict(
        workspace_id=item.workspace.id,
        repository_id=item.repository.id,
        connector_version_id=version_id,
        provider_repository_id="synthetic/42",
    )
    kwargs.update(changes)
    return await _SERVICE.register_source(session, item.actor, **kwargs)


async def counts(session, organization_id):
    return [
        await session.scalar(
            select(func.count()).select_from(model).where(model.organization_id == organization_id)
        )
        for model in (*_FACTS, PolicyDecision, AuditRecord)
    ]


@pytest.mark.asyncio
async def test_management_records_only_ids_and_audits_every_replay(session):
    item = await setup(session)
    version_id = await version(session, item)
    result = await register(session, item, version_id)
    assert result.outcome == "CREATED"
    replay = await register(session, item, version_id)
    assert replay.outcome == "UNCHANGED" and replay.record_id == result.record_id
    assert replay.policy_decision_id != result.policy_decision_id
    assert await counts(session, item.actor.organization_id) == [1, 1, 0, 0, 3, 3]
    audit = await session.scalar(
        select(AuditRecord).where(AuditRecord.policy_decision_id == result.policy_decision_id)
    )
    decision = await session.get(PolicyDecision, result.policy_decision_id)
    assert audit.actor_id == decision.principal_id == item.actor.id
    assert audit.resource_id == str(result.record_id)
    assert decision.effect == "ALLOW" and decision.risk_level == "L2"
    source = await session.get(ProjectSource, result.record_id)
    assert source.created_by == item.actor.id
    payload = (
        json.dumps(audit.redacted_metadata)
        + json.dumps(decision.context)
        + json.dumps(decision.resource)
        + repr(result)
    )
    for omitted in (
        "source.example.invalid",
        "fixture-reference",
        "UNRESOLVED",
        "synthetic/42",
        "configuration",
    ):
        assert omitted not in payload
    revision = ProjectRevision(
        item.actor.organization_id,
        item.workspace.id,
        item.repository.id,
        version_id,
        "a" * 40,
        "b" * 40,
    )
    assert await check_project_source_state(session, revision, environment="test") is None


@pytest.mark.asyncio
async def test_codeup_registration_requires_the_pinned_provider_repository(session):
    item = await setup(session)
    item.repository.name = "synthetic/repo"
    item.connector.connector_type = "codeup"
    item.connector.endpoint = CODEUP_ORIGIN
    item.connector.allowed_egress = ["openapi-rdc.aliyuncs.com:443"]
    item.connector.configuration = {
        "protocol": "codeup.read.v1",
        "organization_id": "org-example",
        "repositories": {
            item.repository.name: {
                "id": "123",
                "repository_id": str(item.repository.id),
            }
        },
        "allowed_repositories": [item.repository.name],
    }
    await session.flush()
    version_id = await version(session, item)

    rejected = await register(
        session,
        item,
        version_id,
        provider_repository_id="999",
    )
    assert rejected.outcome == "DENIED"
    accepted = await register(
        session,
        item,
        version_id,
        provider_repository_id="123",
    )
    assert accepted.outcome == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate",
    [
        "permission",
        "no-policy",
        "DENY",
        "MASK",
        "ASK",
        "obligation",
        "user",
        "organization",
        "foreign-user",
    ],
)
async def test_denials_are_recorded_without_facts_or_payload_privileges(session, gate):
    item = await setup(session)
    actor = replace(item.actor, permissions=frozenset({"*"}), roles=frozenset({"admin"}))
    if gate == "permission":
        await session.execute(
            update(Role)
            .where(Role.id == item.role.id)
            .values(permissions=[])
            .execution_options(synchronize_session=False)
        )
        assert item.role.permissions
    elif gate == "no-policy":
        await session.execute(
            update(Policy).where(Policy.id == item.policy.id).values(enabled=False)
        )
    elif gate in {"DENY", "MASK", "ASK"}:
        await session.execute(
            update(Policy)
            .where(Policy.id == item.policy.id)
            .values(effect=gate)
            .execution_options(synchronize_session=False)
        )
        assert item.policy.effect == "ALLOW"
    elif gate == "obligation":
        item.policy.obligations = [{"type": "unsupported-condition"}]
    elif gate == "user":
        await session.execute(update(User).where(User.id == actor.id).values(active=False))
    elif gate == "organization":
        await session.execute(
            update(Organization)
            .where(Organization.id == actor.organization_id)
            .values(active=False)
        )
    elif gate == "foreign-user":
        other = await setup(session)
        actor = replace(actor, id=other.actor.id)
    result = await _SERVICE.create_connector_version(session, actor, connector_id=item.connector.id)
    assert result.outcome == "DENIED" and result.record_id is None
    assert await counts(session, item.actor.organization_id) == [0, 0, 0, 0, 1, 1]
    audit = await session.scalar(
        select(AuditRecord).where(AuditRecord.organization_id == item.actor.organization_id)
    )
    assert audit.outcome == "DENIED" and audit.policy_decision_id == result.policy_decision_id


@pytest.mark.asyncio
async def test_cached_role_and_policy_revocation_apply_to_replays(session):
    item = await setup(session)
    version_id = await version(session, item)
    assert (await register(session, item, version_id)).outcome == "CREATED"
    await session.execute(
        update(Policy)
        .where(Policy.id == item.policy.id)
        .values(effect="DENY")
        .execution_options(synchronize_session=False)
    )
    assert item.policy.effect == "ALLOW"
    assert (await register(session, item, version_id)).outcome == "DENIED"
    await session.execute(
        update(Policy)
        .where(Policy.id == item.policy.id)
        .values(effect="ALLOW")
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(Role)
        .where(Role.id == item.role.id)
        .values(permissions=[])
        .execution_options(synchronize_session=False)
    )
    assert item.role.permissions
    assert (await register(session, item, version_id)).outcome == "DENIED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("connector_type", "shell"),
        ("environment", "production"),
        ("status", "DISABLED"),
        ("configuration", {"allowed_repositories": ["synthetic/42"], "token": "synthetic-only"}),
        (
            "configuration",
            {
                "allowed_repositories": ["synthetic/42"],
                "headers": {"Authorization": "synthetic-only"},
            },
        ),
        ("configuration", {"allowed_repositories": ["synthetic/42"], "enabled": True}),
        ("configuration", {"allowed_repositories": []}),
        ("configuration", {"allowed_repositories": ["../escape"]}),
        ("endpoint", "https://user:synthetic-only@source.example.invalid"),
        ("endpoint", "https://source.example.invalid?token=synthetic-only"),
        ("endpoint", "https://source.example.invalid/#fragment"),
        ("endpoint", "https://source.example.invalid/repository"),
        ("endpoint", "http://source.example.invalid"),
        ("endpoint", "https://source.example.invalid:99999"),
        ("endpoint", "https://source.example.invalid\n"),
        ("credential_ref", "env://NOT_READ"),
        ("credential_ref", "secret://missing"),
        ("credential_ref", "secret://fixture-reference?token=value"),
        ("declared_grants", ["*"]),
        ("declared_grants", ["code.read", "code.write"]),
        ("allowed_egress", ["*"]),
        ("allowed_egress", ["other.example.invalid"]),
    ],
)
async def test_unsafe_configuration_is_not_frozen_or_logged(session, field, value):
    item = await setup(session)
    await session.execute(
        update(Connector)
        .where(Connector.id == item.connector.id)
        .values(**{field: value})
        .execution_options(synchronize_session=False)
    )
    result = await _SERVICE.create_connector_version(
        session, item.actor, connector_id=item.connector.id
    )
    assert result.outcome in {"DENIED", "INVALID"} and result.record_id is None
    assert await counts(session, item.actor.organization_id) == [0, 0, 0, 0, 1, 1]
    audit = await session.scalar(
        select(AuditRecord).where(AuditRecord.organization_id == item.actor.organization_id)
    )
    assert "synthetic-only" not in json.dumps(audit.redacted_metadata)


@pytest.mark.asyncio
async def test_snapshot_is_detached_and_cannot_follow_current_configuration(session):
    item = await setup(session)
    version_id = await version(session, item)
    frozen = await session.get(ConnectorConfigurationVersion, version_id)
    item.connector.configuration["allowed_repositories"].append("synthetic/99")
    assert frozen.configuration["allowed_repositories"] == ["synthetic/42", "synthetic/43"]
    await session.execute(
        update(Connector)
        .where(Connector.id == item.connector.id)
        .values(configuration={"allowed_repositories": ["synthetic/99"]})
        .execution_options(synchronize_session=False)
    )
    assert (await register(session, item, version_id)).outcome == "DENIED"
    new_version = await version(session, item)
    assert new_version != version_id
    assert (
        await register(session, item, new_version, provider_repository_id="synthetic/99")
    ).outcome == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate",
    [
        "workspace",
        "membership-readonly",
        "repository",
        "deny-grant",
        "archived",
        "deleted",
        "foreign-workspace",
        "foreign-repository",
        "foreign-version",
        "unlisted-provider",
        "url-provider",
    ],
)
async def test_registration_checks_project_acl_and_fixed_binding(session, gate):
    item = await setup(session)
    version_id = await version(session, item)
    changes = {}
    if gate in {"workspace", "membership-readonly"}:
        other = User(
            organization_id=item.actor.organization_id,
            external_id="other",
            display_name="另一个合成用户",
            email="other@example.invalid",
        )
        session.add(other)
        await session.flush()
        item.workspace.owner_id = other.id
        if gate == "membership-readonly":
            session.add(
                WorkspaceMember(
                    organization_id=item.actor.organization_id,
                    workspace_id=item.workspace.id,
                    user_id=item.actor.id,
                    created_by=other.id,
                    can_write=False,
                    permissions=[],
                    created_at=utc_now(),
                )
            )
    elif gate == "repository":
        item.role.permissions = _ACTIONS
    elif gate == "deny-grant":
        session.add(
            CodeRepositoryGrant(
                organization_id=item.actor.organization_id,
                repository_id=item.repository.id,
                subject_type="USER",
                subject_value=str(item.actor.id),
                effect="DENY",
                created_at=utc_now(),
            )
        )
    elif gate == "archived":
        item.workspace.archived_at = utc_now()
    elif gate == "deleted":
        item.repository.deleted_at = utc_now()
    elif gate.startswith("foreign-"):
        other = await setup(session)
        changes[
            {
                "foreign-workspace": "workspace_id",
                "foreign-repository": "repository_id",
                "foreign-version": "connector_version_id",
            }[gate]
        ] = (
            other.workspace.id
            if gate == "foreign-workspace"
            else other.repository.id
            if gate == "foreign-repository"
            else await version(session, other)
        )
    else:
        changes["provider_repository_id"] = (
            "synthetic/99" if gate == "unlisted-provider" else "https://example.invalid/repo"
        )
    result = await register(session, item, version_id, **changes)
    assert result.outcome in {"DENIED", "INVALID"} and result.record_id is None
    assert (await counts(session, item.actor.organization_id))[1] == 0


@pytest.mark.asyncio
async def test_explicit_membership_and_grant_allow_without_admin_bypass(session):
    item = await setup(session)
    version_id = await version(session, item)
    other = User(
        organization_id=item.actor.organization_id,
        external_id="other",
        display_name="合成所有者",
        email="other@example.invalid",
    )
    session.add(other)
    await session.flush()
    item.workspace.owner_id = other.id
    item.role.permissions = _ACTIONS
    session.add_all(
        [
            WorkspaceMember(
                organization_id=item.actor.organization_id,
                workspace_id=item.workspace.id,
                user_id=item.actor.id,
                created_by=other.id,
                can_write=True,
                permissions=[],
                created_at=utc_now(),
            ),
            CodeRepositoryGrant(
                organization_id=item.actor.organization_id,
                repository_id=item.repository.id,
                subject_type="USER",
                subject_value=str(item.actor.id),
                effect="ALLOW",
                created_at=utc_now(),
            ),
        ]
    )
    assert (await register(session, item, version_id)).outcome == "CREATED"
    await session.execute(
        delete(WorkspaceMember).where(WorkspaceMember.workspace_id == item.workspace.id)
    )
    assert (await register(session, item, version_id)).outcome == "DENIED"


@pytest.mark.asyncio
async def test_different_provider_cannot_rebind_same_fixed_tuple(session):
    item = await setup(session)
    version_id = await version(session, item)
    result = await register(session, item, version_id)
    conflict = await register(session, item, version_id, provider_repository_id="synthetic/43")
    assert conflict.outcome == "CONFLICT" and conflict.record_id is None
    assert (
        await session.get(ProjectSource, result.record_id)
    ).provider_repository_id == "synthetic/42"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["source", "version"])
async def test_revocation_is_idempotent_and_does_not_restore_source(session, target):
    item = await setup(session)
    version_id = await version(session, item)
    source = await register(session, item, version_id)
    # 即使配置损坏/禁用，仍允许有独立撤销权限的主体终止旧来源。
    item.connector.status = "DISABLED"
    item.connector.configuration = {"no-longer-valid": True}
    method = _SERVICE.revoke_source if target == "source" else _SERVICE.revoke_connector_version
    kwargs = (
        {"source_id": source.record_id}
        if target == "source"
        else {"connector_version_id": version_id}
    )
    first = await method(session, item.actor, **kwargs, reason="OPERATOR_REVOKED")
    repeat = await method(session, item.actor, **kwargs, reason="SECURITY_REVOKED")
    assert first.outcome == "CREATED" and repeat.outcome == "UNCHANGED"
    model = ProjectSourceRevocation if target == "source" else ConnectorVersionRevocation
    revoked = await session.get(model, first.record_id)
    assert revoked.reason_code == "OPERATOR_REVOKED" and revoked.revoked_by == item.actor.id
    item.connector.status = "ACTIVE"
    item.connector.configuration = {"allowed_repositories": ["synthetic/42", "synthetic/43"]}
    assert (await register(session, item, version_id)).outcome == "DENIED"
    revision = ProjectRevision(
        item.actor.organization_id,
        item.workspace.id,
        item.repository.id,
        version_id,
        "a" * 40,
        "b" * 40,
    )
    with pytest.raises(ProjectRejected):
        await check_project_source_state(session, revision, environment="test")
    item.role.permissions = []
    assert (
        await method(session, item.actor, **kwargs, reason="SECURITY_REVOKED")
    ).outcome == "DENIED"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["version", "source", "version-revoke", "source-revoke"])
async def test_audit_failure_rolls_back_fact_and_policy_even_when_caller_catches(
    session, operation
):
    item = await setup(session)
    version_id = await version(session, item)
    source = await register(session, item, version_id)
    before = await counts(session, item.actor.organization_id)
    service = ProjectSourceService(environment="test")

    class BrokenAudit:
        async def write(self, session, draft):
            raise RuntimeError("synthetic-audit-failure")

    service.audit = BrokenAudit()
    with pytest.raises(RuntimeError, match="synthetic-audit-failure"):
        if operation == "version":
            await service.create_connector_version(
                session, item.actor, connector_id=item.connector.id
            )
        elif operation == "source":
            await service.register_source(
                session,
                item.actor,
                workspace_id=item.workspace.id,
                repository_id=item.repository.id,
                connector_version_id=await version(session, item),
                provider_repository_id="synthetic/42",
            )
        elif operation == "version-revoke":
            await service.revoke_connector_version(
                session, item.actor, connector_version_id=version_id, reason="OPERATOR_REVOKED"
            )
        else:
            await service.revoke_source(
                session, item.actor, source_id=source.record_id, reason="OPERATOR_REVOKED"
            )
    after = await counts(session, item.actor.organization_id)
    # source 分支中的新版本在进入失败服务之前单独成功，不属于失败 SAVEPOINT。
    assert after == (
        [before[0] + 1, *before[1:4], before[4] + 1, before[5] + 1]
        if operation == "source"
        else before
    )
    assert (await register(session, item, version_id)).outcome == "UNCHANGED"


@pytest.mark.asyncio
async def test_caller_savepoint_rollback_undoes_all_management_records(session):
    item = await setup(session)
    before = await counts(session, item.actor.organization_id)
    async with session.begin_nested() as savepoint:
        version_id = await version(session, item)
        source = await register(session, item, version_id)
        await _SERVICE.revoke_source(
            session, item.actor, source_id=source.record_id, reason="OPERATOR_REVOKED"
        )
        await savepoint.rollback()
    assert await counts(session, item.actor.organization_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["source", "version"])
@pytest.mark.parametrize("gate", ["foreign-tenant", "wrong-environment", "invalid-reason"])
async def test_revocation_cannot_cross_tenant_or_environment(session, target, gate):
    item = await setup(session)
    version_id = await version(session, item)
    source = await register(session, item, version_id)
    service = _SERVICE
    actor = item.actor
    reason = "OPERATOR_REVOKED"
    if gate == "foreign-tenant":
        actor = (await setup(session)).actor
    elif gate == "wrong-environment":
        service = ProjectSourceService(environment="staging")
        item.policy.conditions = {"actions": _ACTIONS}
    else:
        reason = "arbitrary-sensitive-text"
    if target == "source":
        result = await service.revoke_source(
            session, actor, source_id=source.record_id, reason=reason
        )
    else:
        result = await service.revoke_connector_version(
            session, actor, connector_version_id=version_id, reason=reason
        )
    assert result.outcome in {"DENIED", "INVALID"} and result.record_id is None
    assert (await counts(session, item.actor.organization_id))[2:4] == [0, 0]


@pytest.mark.asyncio
async def test_management_requires_caller_transaction(session):
    factory = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    async with factory() as fresh:
        with pytest.raises(ValueError, match="requires_transaction"):
            await _SERVICE.create_connector_version(
                fresh, Principal(uuid4(), uuid4(), "", ""), connector_id=uuid4()
            )
        assert not fresh.in_transaction()


@pytest.mark.asyncio
async def test_unknown_tenant_and_foreign_connector_never_create_records(session):
    item = await setup(session)
    other = await setup(session)
    unknown = replace(item.actor, organization_id=uuid4())
    result = await _SERVICE.create_connector_version(
        session, unknown, connector_id=item.connector.id
    )
    assert result.outcome == "DENIED" and result.policy_decision_id is None
    assert (
        await _SERVICE.create_connector_version(
            session, item.actor, connector_id=other.connector.id
        )
    ).outcome == "DENIED"


def test_internal_contract_rejects_production_and_caller_snapshot():
    with pytest.raises(ValueError, match="environment_denied"):
        ProjectSourceService(environment="production")
    import inspect

    assert (
        "configuration"
        not in inspect.signature(ProjectSourceService.create_connector_version).parameters
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["configuration", "policy", "permission", "revocation"])
async def test_postgres_lock_wait_rechecks_current_configuration_and_authority(change):
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL lock-wait test requires explicit opt-in")
    url = os.getenv("OBSION_DATABASE_URL", "")
    assert url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    task = None
    try:
        async with sessions.begin() as initial:
            item = await setup(initial)
            version_id = await version(initial, item)
        async with sessions.begin() as holder:
            await holder.scalar(
                select(Connector.id).where(Connector.id == item.connector.id).with_for_update()
            )
            pid_ready = asyncio.get_running_loop().create_future()

            async def waiting_registration():
                async with sessions.begin() as waiting:
                    # 特意预热 ORM 缓存，然后等待另一事务持有的 Connector 行锁。
                    await waiting.get(Role, item.role.id)
                    await waiting.get(Policy, item.policy.id)
                    pid_ready.set_result(await waiting.scalar(text("SELECT pg_backend_pid()")))
                    return await register(waiting, item, version_id)

            task = asyncio.create_task(waiting_registration())
            pid = await asyncio.wait_for(pid_ready, 5)
            async with sessions() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.02)
                else:
                    pytest.fail("管理事务没有实际等待 PostgreSQL 行锁")
            assert not task.done()
            if change == "configuration":
                await holder.execute(
                    update(Connector)
                    .where(Connector.id == item.connector.id)
                    .values(configuration={"allowed_repositories": ["synthetic/99"]})
                )
            elif change == "policy":
                await holder.execute(
                    update(Policy).where(Policy.id == item.policy.id).values(effect="DENY")
                )
            elif change == "permission":
                await holder.execute(
                    update(Role).where(Role.id == item.role.id).values(permissions=[])
                )
            else:
                result = await _SERVICE.revoke_connector_version(
                    holder, item.actor, connector_version_id=version_id, reason="SECURITY_REVOKED"
                )
                assert result.outcome == "CREATED"
        result = await asyncio.wait_for(task, 10)
        assert result.outcome == "DENIED" and result.record_id is None
        async with sessions.begin() as verification:
            assert (await counts(verification, item.actor.organization_id))[1] == 0
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_concurrent_registration_and_revocation():
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL source management concurrency requires explicit opt-in")
    url = os.getenv("OBSION_DATABASE_URL", "")
    assert url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as initial:
            item = await setup(initial)
            version_id = await version(initial, item)

        async def bind():
            async with sessions.begin() as current:
                return await register(current, item, version_id)

        results = await asyncio.wait_for(asyncio.gather(*(bind() for _ in range(12))), 30)
        assert [r.outcome for r in results].count("CREATED") == 1
        assert [r.outcome for r in results].count("UNCHANGED") == 11
        assert len({r.record_id for r in results}) == 1

        async def revoke():
            async with sessions.begin() as current:
                return await _SERVICE.revoke_source(
                    current, item.actor, source_id=results[0].record_id, reason="SECURITY_REVOKED"
                )

        revocations = await asyncio.wait_for(asyncio.gather(*(revoke() for _ in range(12))), 30)
        assert [r.outcome for r in revocations].count("CREATED") == 1
        assert [r.outcome for r in revocations].count("UNCHANGED") == 11
        assert (await bind()).outcome == "DENIED"
        async with sessions.begin() as current:
            assert await counts(current, item.actor.organization_id) == [1, 1, 0, 1, 26, 26]
    finally:
        await engine.dispose()
