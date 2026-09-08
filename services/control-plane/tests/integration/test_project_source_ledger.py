"""SQLite 查询与真实 PostgreSQL 账本不变量分开计数，不用前者代替触发器。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from obsion.common.time import utc_now
from obsion.db.base import Base
from obsion.db.models import CodeRepository, Connector, Organization, User, Workspace
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
)
from obsion.sandbox.project import ProjectRejected, ProjectRevision
from obsion.sandbox.source_state import check_project_source_state

_MODELS = (
    Organization,
    User,
    Workspace,
    Connector,
    CodeRepository,
    ConnectorConfigurationVersion,
    ProjectSource,
    ConnectorVersionRevocation,
    ProjectSourceRevocation,
)


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def session(request, tmp_path):
    if request.param == "postgresql":
        if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
            pytest.skip("PostgreSQL project source tests require explicit opt-in")
        url = os.getenv("OBSION_DATABASE_URL", "")
        assert url.startswith("postgresql+asyncpg://"), "An explicit PostgreSQL URL is required"
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'source-ledger.db'}"
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            if request.param == "sqlite":
                await connection.execute(sa.text("PRAGMA foreign_keys=ON"))
                # User 的 department FK 还需要 departments，但无需整个应用 schema。
                tables = {model.__tablename__ for model in _MODELS} | {"departments"}
                await connection.run_sync(
                    lambda conn: Base.metadata.create_all(
                        conn, tables=[t for t in Base.metadata.sorted_tables if t.name in tables]
                    )
                )
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
class Binding:
    revision: ProjectRevision
    connector: Connector
    version: ConnectorConfigurationVersion
    source: ProjectSource
    user_id: UUID


async def binding(session: AsyncSession) -> Binding:
    organization = Organization(slug=f"source-{uuid4()}", name="合成来源组织")
    session.add(organization)
    await session.flush()
    user = User(
        organization_id=organization.id,
        external_id="synthetic-user",
        email="source@example.invalid",
        display_name="合成管理员",
    )
    session.add(user)
    await session.flush()
    workspace = Workspace(organization_id=organization.id, name="合成项目", owner_id=user.id)
    connector = Connector(
        organization_id=organization.id,
        name="synthetic-source",
        connector_type="git-http",
        status="ACTIVE",
        environment="test",
        endpoint="https://source.example.invalid",
        configuration={"allowed_repositories": ["synthetic/42"], "enabled": True},
        declared_grants=["code.read"],
        allowed_egress=["source.example.invalid"],
        credential_ref="secret://synthetic-reference",
    )
    repository = CodeRepository(
        organization_id=organization.id,
        name="合成仓库",
        classification="INTERNAL",
        acl={},
    )
    session.add_all([workspace, connector, repository])
    await session.flush()
    version = ConnectorConfigurationVersion(
        organization_id=organization.id,
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
        organization_id=organization.id,
        workspace_id=workspace.id,
        repository_id=repository.id,
        connector_version_id=version.id,
        provider_repository_id="synthetic/42",
        created_by=user.id,
    )
    session.add(source)
    await session.flush()
    revision = ProjectRevision(
        organization.id, workspace.id, repository.id, version.id, "a" * 40, "b" * 40
    )
    return Binding(revision, connector, version, source, user.id)


@pytest.mark.asyncio
async def test_fixed_source_is_only_a_state_check(session):
    item = await binding(session)
    assert await check_project_source_state(session, item.revision, environment="test") is None
    # 状态检查不声称实际 scopes / 用户 ACL / Git 对象正确，调用者仍须独立验证。
    await check_project_source_state(
        session, replace(item.revision, commit_id="c" * 40), environment="test"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["organization_id", "workspace_id", "repository_id", "connector_version_id"]
)
async def test_wrong_fixed_identity_is_unavailable(session, field):
    item = await binding(session)
    other = await binding(session)
    revision = replace(item.revision, **{field: getattr(other.revision, field)})
    with pytest.raises(ProjectRejected, match="^project_source_unavailable$"):
        await check_project_source_state(session, revision, environment="test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("connector_type", "different"),
        ("environment", "staging"),
        ("endpoint", "https://changed.example.invalid"),
        ("configuration", {"allowed_repositories": ["synthetic/42"], "enabled": 1}),
        ("credential_ref", "secret://different-reference"),
        ("declared_grants", ["*"]),
        ("allowed_egress", ["changed.example.invalid"]),
    ],
)
async def test_configuration_drift_never_follows_latest_or_orm_cache(session, field, value):
    item = await binding(session)
    old = getattr(item.connector, field)
    await check_project_source_state(session, item.revision, environment="test")
    await session.execute(
        sa.update(Connector)
        .where(Connector.id == item.connector.id)
        .values(**{field: value})
        .execution_options(synchronize_session=False)
    )
    assert getattr(item.connector, field) == old
    with pytest.raises(ProjectRejected, match="^project_source_unavailable$"):
        await check_project_source_state(session, item.revision, environment="test")
    assert getattr(item.version, field) == old


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["organization", "workspace", "repository", "connector", "source", "version", "environment"],
)
async def test_disabled_or_revoked_source_is_unavailable(session, target):
    item = await binding(session)
    await check_project_source_state(session, item.revision, environment="test")
    if target == "organization":
        await session.execute(
            sa.update(Organization)
            .where(Organization.id == item.revision.organization_id)
            .values(active=False)
        )
    elif target == "workspace":
        await session.execute(
            sa.update(Workspace)
            .where(Workspace.id == item.revision.workspace_id)
            .values(archived_at=utc_now())
        )
    elif target == "repository":
        await session.execute(
            sa.update(CodeRepository)
            .where(CodeRepository.id == item.revision.repository_id)
            .values(deleted_at=utc_now())
        )
    elif target == "connector":
        await session.execute(
            sa.update(Connector).where(Connector.id == item.connector.id).values(status="DISABLED")
        )
    elif target == "source":
        session.add(
            ProjectSourceRevocation(
                organization_id=item.revision.organization_id,
                source_id=item.source.id,
                revoked_by=item.user_id,
                reason_code="OPERATOR_REVOKED",
            )
        )
    elif target == "version":
        session.add(
            ConnectorVersionRevocation(
                organization_id=item.revision.organization_id,
                connector_version_id=item.version.id,
                revoked_by=item.user_id,
                reason_code="SECURITY_REVOKED",
            )
        )
    # source/version 故意不 flush：Core 查询前必须看到当前事务待写的撤销。
    with pytest.raises(ProjectRejected, match="^project_source_unavailable$"):
        await check_project_source_state(
            session, item.revision, environment="staging" if target == "environment" else "test"
        )


@pytest.mark.asyncio
async def test_health_and_name_are_not_configuration_drift(session):
    item = await binding(session)
    await session.execute(
        sa.update(Connector)
        .where(Connector.id == item.connector.id)
        .values(name="renamed", last_health={"status": "ok"})
    )
    await check_project_source_state(session, item.revision, environment="test")


async def rejected(session, statement, *, state):
    async with session.begin_nested() as savepoint:
        with pytest.raises(DBAPIError) as error:
            await session.execute(statement)
        if session.bind.dialect.name == "postgresql":
            assert getattr(error.value.orig, "sqlstate", None) == state
        await savepoint.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table,column",
    [
        (ConnectorConfigurationVersion, "connector_id"),
        (ConnectorConfigurationVersion, "created_by"),
        (ProjectSource, "workspace_id"),
        (ProjectSource, "repository_id"),
        (ProjectSource, "connector_version_id"),
        (ProjectSource, "created_by"),
        (ConnectorVersionRevocation, "connector_version_id"),
        (ConnectorVersionRevocation, "revoked_by"),
        (ProjectSourceRevocation, "source_id"),
        (ProjectSourceRevocation, "revoked_by"),
    ],
)
async def test_cross_tenant_foreign_keys(session, table, column):
    item, other = await binding(session), await binding(session)
    if table is ConnectorConfigurationVersion:
        values = {c.name: getattr(item.version, c.name) for c in table.__table__.columns}
        values["id"] = uuid4()
        values[column] = other.connector.id if column == "connector_id" else other.user_id
    elif table is ProjectSource:
        workspace = Workspace(
            organization_id=item.revision.organization_id,
            name="另一合成项目",
            owner_id=item.user_id,
        )
        session.add(workspace)
        await session.flush()
        values = {c.name: getattr(item.source, c.name) for c in table.__table__.columns}
        values.update(id=uuid4(), workspace_id=workspace.id)
        values[column] = (
            other.user_id if column == "created_by" else getattr(other.revision, column)
        )
    else:
        key = "source_id" if table is ProjectSourceRevocation else "connector_version_id"
        values = dict(
            organization_id=item.revision.organization_id,
            revoked_by=item.user_id,
            reason_code="OPERATOR_REVOKED",
        )
        values[key] = item.source.id if key == "source_id" else item.version.id
        values[column] = (
            other.user_id
            if column == "revoked_by"
            else (other.source.id if key == "source_id" else other.version.id)
        )
    await rejected(session, sa.insert(table).values(**values), state="23503")


@pytest.mark.asyncio
async def test_same_fixed_identity_cannot_be_rebound(session):
    item = await binding(session)
    values = {c.name: getattr(item.source, c.name) for c in ProjectSource.__table__.columns}
    values.update(id=uuid4(), provider_repository_id="synthetic/different")
    await rejected(session, sa.insert(ProjectSource).values(**values), state="23505")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["workspace_id", "repository_id", "connector_version_id"])
async def test_same_tenant_unbound_identity_is_not_a_source(session, field):
    item = await binding(session)
    if field == "workspace_id":
        other = Workspace(
            organization_id=item.revision.organization_id, name="未绑定项目", owner_id=item.user_id
        )
    elif field == "repository_id":
        other = CodeRepository(
            organization_id=item.revision.organization_id,
            name="未绑定仓库",
            classification="INTERNAL",
            acl={},
        )
    else:
        values = {
            c.name: getattr(item.version, c.name)
            for c in ConnectorConfigurationVersion.__table__.columns
        }
        values["id"] = uuid4()
        other = ConnectorConfigurationVersion(**values)
    session.add(other)
    await session.flush()
    with pytest.raises(ProjectRejected, match="^project_source_unavailable$"):
        await check_project_source_state(
            session, replace(item.revision, **{field: other.id}), environment="test"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,key",
    [(ProjectSourceRevocation, "source_id"), (ConnectorVersionRevocation, "connector_version_id")],
)
async def test_revocation_cannot_be_duplicated_or_undone_by_config_restore(session, model, key):
    item = await binding(session)
    values = dict(
        organization_id=item.revision.organization_id,
        revoked_by=item.user_id,
        reason_code="OPERATOR_REVOKED",
    )
    values[key] = item.source.id if key == "source_id" else item.version.id
    await session.execute(sa.insert(model).values(**values))
    await rejected(session, sa.insert(model).values(**values), state="23505")
    item.connector.status = "DISABLED"
    await session.flush()
    item.connector.status = "ACTIVE"
    with pytest.raises(ProjectRejected, match="^project_source_unavailable$"):
        await check_project_source_state(session, item.revision, environment="test")


@pytest.mark.asyncio
async def test_postgres_all_fact_columns_and_deletion_are_immutable(session):
    if session.bind.dialect.name != "postgresql":
        pytest.skip("SQLite does not install PostgreSQL immutable triggers")
    item = await binding(session)
    version_revoke = ConnectorVersionRevocation(
        organization_id=item.revision.organization_id,
        connector_version_id=item.version.id,
        revoked_by=item.user_id,
        reason_code="OPERATOR_REVOKED",
    )
    source_revoke = ProjectSourceRevocation(
        organization_id=item.revision.organization_id,
        source_id=item.source.id,
        revoked_by=item.user_id,
        reason_code="OPERATOR_REVOKED",
    )
    session.add_all([version_revoke, source_revoke])
    await session.flush()
    for record in (item.version, item.source, version_revoke, source_revoke):
        table = record.__table__
        key = next(iter(table.primary_key.columns))
        where = key == getattr(record, key.name)
        for column in table.columns:
            old = getattr(record, column.name)
            if isinstance(old, UUID):
                changed = uuid4()
            elif isinstance(old, dict):
                changed = {"changed": True}
            elif isinstance(old, list):
                changed = ["changed"]
            elif isinstance(column.type, sa.DateTime):
                changed = old - timedelta(days=1)
            else:
                changed = "changed"
            await rejected(
                session,
                sa.update(table).where(where).values(**{column.name: changed}),
                state="23000",
            )
        await rejected(session, sa.delete(table).where(where), state="23000")
        # 连同所有引用表 TRUNCATE，避免 FK 先拒绝而冒充触发器验证。
        await rejected(session, sa.text(f"TRUNCATE {table.name} CASCADE"), state="23000")
    await session.execute(
        sa.update(Connector)
        .where(Connector.id == item.connector.id)
        .values(configuration={"changed": True})
    )
    await session.execute(
        sa.update(Connector)
        .where(Connector.id == item.connector.id)
        .values(configuration=item.version.configuration)
    )
    with pytest.raises(ProjectRejected, match="^project_source_unavailable$"):
        await check_project_source_state(session, item.revision, environment="test")
