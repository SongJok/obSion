"""仅在显式启用的独立空 PostgreSQL 库验证来源账本迁移与保留。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import obsion.config as obsion_config
from obsion.config import Settings
from obsion.db.models import CodeRepository, Connector, Organization, User, Workspace
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
    RunSourcePin,
)

_ROOT = Path(__file__).resolve().parents[4]
_BASE = "a83d14e25f36"
_TABLES = (
    ConnectorConfigurationVersion,
    ProjectSource,
    ConnectorVersionRevocation,
    ProjectSourceRevocation,
    RunSourcePin,
)


def test_project_source_migration_round_trip_and_nonempty_guard(monkeypatch):
    if os.getenv("OBSION_RUN_PROJECT_SOURCE_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL source migration test requires explicit opt-in")
    url = os.getenv("OBSION_DATABASE_URL", "")
    assert url.startswith("postgresql+asyncpg://"), "An explicit PostgreSQL URL is required"
    settings = Settings(_env_file=None, database_url=url)
    asyncio.run(_require_empty(url))
    monkeypatch.setattr(obsion_config, "get_settings", lambda: settings)
    config = Config(str(_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "services/control-plane/alembic"))
    config.set_main_option("sqlalchemy.url", url)
    head = ScriptDirectory.from_config(config).get_current_head()
    command.upgrade(config, _BASE)
    legacy = asyncio.run(_legacy(url))
    for _ in range(2):
        command.upgrade(config, "head")
        command.check(config)
        asyncio.run(_empty_ledger_and_legacy(url, legacy))
        command.downgrade(config, _BASE)
    command.upgrade(config, "head")
    command.check(config)
    asyncio.run(_persist_version(url, legacy))
    # 仅版本事实也必须阻止降级，不能只检查是否已有项目/撤销。
    with pytest.raises(RuntimeError, match="populated project source ledger"):
        command.downgrade(config, _BASE)
    asyncio.run(_assert_preserved(url, head, [1, 0, 0, 0, 0]))
    asyncio.run(_persist_binding_and_revocations(url, legacy))
    with pytest.raises(RuntimeError, match="populated project source ledger"):
        command.downgrade(config, _BASE)
    asyncio.run(_assert_preserved(url, head, [1, 1, 1, 1, 0]))
    command.upgrade(config, "head")
    command.check(config)


async def _require_empty(url):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
            assert count == 0, "Source migration test refuses a populated database"
    finally:
        await engine.dispose()


async def _legacy(url):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            org = Organization(slug=f"source-migration-{uuid4()}", name="合成组织")
            session.add(org)
            await session.flush()
            user = User(
                organization_id=org.id,
                external_id="synthetic",
                email="source@example.invalid",
                display_name="合成用户",
            )
            session.add(user)
            await session.flush()
            connector = Connector(
                organization_id=org.id,
                name="合成来源",
                connector_type="git-http",
                status="ACTIVE",
                environment="test",
                configuration={"synthetic": True},
            )
            repository = CodeRepository(
                organization_id=org.id,
                name="合成仓库",
                classification="INTERNAL",
                acl={"users": [str(user.id)]},
            )
            workspace = Workspace(organization_id=org.id, name="合成项目", owner_id=user.id)
            session.add_all([connector, repository, workspace])
            await session.flush()
            return dict(
                org=org.id,
                user=user.id,
                connector=connector.id,
                repository=repository.id,
                workspace=workspace.id,
                version=uuid4(),
                source=uuid4(),
            )
    finally:
        await engine.dispose()


async def _empty_ledger_and_legacy(url, ids):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            for model in _TABLES:
                assert await connection.scalar(sa.select(sa.func.count()).select_from(model)) == 0
            assert await connection.scalar(
                sa.select(Connector.configuration).where(Connector.id == ids["connector"])
            ) == {"synthetic": True}
            assert await connection.scalar(
                sa.select(CodeRepository.acl).where(CodeRepository.id == ids["repository"])
            ) == {"users": [str(ids["user"])]}
    finally:
        await engine.dispose()


async def _persist_version(url, ids):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.insert(ConnectorConfigurationVersion).values(
                    id=ids["version"],
                    organization_id=ids["org"],
                    connector_id=ids["connector"],
                    connector_type="git-http",
                    environment="test",
                    configuration={"synthetic": True},
                    declared_grants=[],
                    allowed_egress=[],
                    created_by=ids["user"],
                )
            )
    finally:
        await engine.dispose()


async def _persist_binding_and_revocations(url, ids):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.insert(ProjectSource).values(
                    id=ids["source"],
                    organization_id=ids["org"],
                    workspace_id=ids["workspace"],
                    repository_id=ids["repository"],
                    connector_version_id=ids["version"],
                    provider_repository_id="synthetic/42",
                    created_by=ids["user"],
                )
            )
            await connection.execute(
                sa.insert(ConnectorVersionRevocation).values(
                    organization_id=ids["org"],
                    connector_version_id=ids["version"],
                    revoked_by=ids["user"],
                    reason_code="OPERATOR_REVOKED",
                )
            )
            await connection.execute(
                sa.insert(ProjectSourceRevocation).values(
                    organization_id=ids["org"],
                    source_id=ids["source"],
                    revoked_by=ids["user"],
                    reason_code="SECURITY_REVOKED",
                )
            )
    finally:
        await engine.dispose()


async def _assert_preserved(url, head, counts):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == head
            )
            for model, count in zip(_TABLES, counts, strict=True):
                assert (
                    await connection.scalar(sa.select(sa.func.count()).select_from(model)) == count
                )
            assert await connection.scalar(
                sa.select(ConnectorConfigurationVersion.configuration)
            ) == {"synthetic": True}
            if counts[3]:
                assert (
                    await connection.scalar(sa.select(ProjectSourceRevocation.reason_code))
                    == "SECURITY_REVOKED"
                )
                assert (
                    await connection.scalar(sa.select(ConnectorVersionRevocation.reason_code))
                    == "OPERATOR_REVOKED"
                )
    finally:
        await engine.dispose()
