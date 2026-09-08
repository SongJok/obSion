"""在独立空 PostgreSQL 库中验证 Memory 撤销迁移，不接触部署环境文件。"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

import obsion.config as obsion_config
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import Memory, Organization

_ROOT = Path(__file__).resolve().parents[4]
_BASE = "a82c03d14e25"
_STATUSES = ("CANDIDATE", "APPROVED", "REJECTED", "EXPIRED", "REVOKED")
_ALLOWED = {
    ("CANDIDATE", "APPROVED"),
    ("CANDIDATE", "REJECTED"),
    ("CANDIDATE", "EXPIRED"),
    ("CANDIDATE", "REVOKED"),
    ("APPROVED", "EXPIRED"),
    ("APPROVED", "REVOKED"),
}


def test_memory_revoke_guard_migration_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("OBSION_RUN_MEMORY_REVOKE_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL Memory migration test requires explicit opt-in")
    database_url = os.getenv("OBSION_DATABASE_URL", "")
    assert database_url.startswith("postgresql+asyncpg://"), (
        "An explicit PostgreSQL URL is required"
    )
    settings = Settings(_env_file=None, database_url=database_url)
    asyncio.run(_require_empty_database(database_url))
    monkeypatch.setattr(obsion_config, "get_settings", lambda: settings)
    config = Config(str(_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "services/control-plane/alembic"))
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, _BASE)
    # 先证实旧 CHECK 虽允许 REVOKED，旧触发器确实拒绝合法撤销。
    asyncio.run(_verify_old_guard(database_url))
    command.upgrade(config, "head")
    command.check(config)
    asyncio.run(_verify_new_guard(database_url))
    command.downgrade(config, _BASE)
    asyncio.run(_verify_old_guard(database_url))
    command.upgrade(config, "head")
    command.check(config)
    asyncio.run(_verify_new_guard(database_url))

    memory_id = asyncio.run(_persist_revoked(database_url))
    with pytest.raises(RuntimeError, match="REVOKED records exist"):
        command.downgrade(config, _BASE)
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    asyncio.run(_verify_preserved(database_url, memory_id, head))


async def _require_empty_database(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            tables = await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
            assert tables == 0, "Memory migration test refuses a populated database"
    finally:
        await engine.dispose()


async def _organization(connection: AsyncConnection) -> UUID:
    organization_id = uuid4()
    await connection.execute(
        sa.insert(Organization).values(
            id=organization_id,
            slug=f"memory-migration-{organization_id}",
            name="Memory migration fixture",
            active=True,
            settings={},
        )
    )
    return organization_id


async def _memory(connection: AsyncConnection, organization_id: UUID, status: str) -> UUID:
    memory_id = uuid4()
    await connection.execute(
        sa.insert(Memory).values(
            id=memory_id,
            organization_id=organization_id,
            scope="USER",
            owner_ref=str(uuid4()),
            content={"preference": "使用 UTC"},
            dedupe_key=memory_id.hex,
            sensitivity="INTERNAL",
            status=status,
            expires_at=utc_now() + timedelta(days=1),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    return memory_id


async def _rejected(connection: AsyncConnection, statement: sa.Executable) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(statement)
        # 必须是受治理触发器拒绝，不能以外键/类型失败冒充不可变约束。
        assert getattr(error.value.orig, "sqlstate", None) == "23000"
    finally:
        await savepoint.rollback()


async def _verify_old_guard(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                organization_id = await _organization(connection)
                for status in ("CANDIDATE", "APPROVED"):
                    memory_id = await _memory(connection, organization_id, status)
                    await _rejected(
                        connection,
                        sa.update(Memory).where(Memory.id == memory_id).values(status="REVOKED"),
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _verify_new_guard(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                organization_id = await _organization(connection)
                other_organization_id = await _organization(connection)
                for old in _STATUSES:
                    for new in _STATUSES:
                        memory_id = await _memory(connection, organization_id, old)
                        statement = (
                            sa.update(Memory).where(Memory.id == memory_id).values(status=new)
                        )
                        if old == new or (old, new) in _ALLOWED:
                            await connection.execute(statement)
                            assert (
                                await connection.scalar(
                                    sa.select(Memory.status).where(Memory.id == memory_id)
                                )
                                == new
                            )
                        else:
                            await _rejected(connection, statement)

                for status in _STATUSES:
                    memory_id = await _memory(connection, organization_id, status)
                    for values in (
                        {"organization_id": other_organization_id},
                        {"scope": "WORKSPACE"},
                        {"owner_ref": str(uuid4())},
                        {"content": {"changed": True}},
                        {"dedupe_key": "b" * 64},
                        {"sensitivity": "PUBLIC"},
                        {"policy_decision_id": uuid4()},
                        {"created_at": utc_now() - timedelta(days=1)},
                    ):
                        await _rejected(
                            connection,
                            sa.update(Memory).where(Memory.id == memory_id).values(**values),
                        )
                    await _rejected(connection, sa.delete(Memory).where(Memory.id == memory_id))

                # 撤销与篡改同一个 UPDATE 不能趁合法状态转换修改内容。
                memory_id = await _memory(connection, organization_id, "APPROVED")
                await _rejected(
                    connection,
                    sa.update(Memory)
                    .where(Memory.id == memory_id)
                    .values(status="REVOKED", content={"changed": True}),
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _persist_revoked(database_url: str) -> UUID:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            organization_id = await _organization(connection)
            memory_id = await _memory(connection, organization_id, "APPROVED")
            await connection.execute(
                sa.update(Memory).where(Memory.id == memory_id).values(status="REVOKED")
            )
            return memory_id
    finally:
        await engine.dispose()


async def _verify_preserved(database_url: str, memory_id: UUID, head: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == head
            )
            assert (
                await connection.scalar(sa.select(Memory.status).where(Memory.id == memory_id))
                == "REVOKED"
            )
            assert await connection.scalar(
                sa.select(Memory.content).where(Memory.id == memory_id)
            ) == {"preference": "使用 UTC"}
    finally:
        await engine.dispose()
