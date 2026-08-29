from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings

_REPOSITORY_ROOT = Path(__file__).parents[4]
_PREVIOUS_REVISION = "e6f9a0123bcd"
_RENAME_REVISION = "f7a1b2c3d4e5"
_ORGANIZATION_ID = UUID("018f47ca-4a8c-7df5-9ad3-222222222222")
_AUDIT_ID = UUID("018f47ca-4a8c-7df5-9ad3-333333333333")

_CONSTRAINT_RENAMES = {
    "pk_audit_records": "pk_audit_logs",
    "fk_audit_records_approval_id_approvals": "fk_audit_logs_approval_id_approvals",
    "fk_audit_records_organization_id_organizations": (
        "fk_audit_logs_organization_id_organizations"
    ),
    "fk_audit_records_policy_decision_id_policy_decisions": (
        "fk_audit_logs_policy_decision_id_policy_decisions"
    ),
}
_INDEX_RENAMES = {
    "pk_audit_records": "pk_audit_logs",
    "ix_audit_records_action": "ix_audit_logs_action",
    "ix_audit_records_correlation_id": "ix_audit_logs_correlation_id",
    "ix_audit_records_organization_id": "ix_audit_logs_organization_id",
}
_TRIGGER_RENAMES = {
    "trg_audit_records_immutable": "trg_audit_logs_immutable",
}


def test_audit_log_rename_preserves_data_objects_and_immutability() -> None:
    if os.getenv("OBSION_RUN_AUDIT_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL migration test is opt-in")

    config = _alembic_config()
    command.upgrade(config, _PREVIOUS_REVISION)
    before = asyncio.run(_insert_and_snapshot())

    command.upgrade(config, _RENAME_REVISION)
    after_upgrade = asyncio.run(_assert_phase("audit_logs", before, old=False))

    command.downgrade(config, _PREVIOUS_REVISION)
    after_downgrade = asyncio.run(_assert_phase("audit_records", before, old=True))

    command.upgrade(config, _RENAME_REVISION)
    after_reupgrade = asyncio.run(_assert_phase("audit_logs", before, old=False))

    assert after_upgrade == after_reupgrade
    assert after_downgrade == before


def _alembic_config() -> Config:
    config = Config(str(_REPOSITORY_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


async def _insert_and_snapshot() -> dict[str, Any]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO organizations (
                        id, slug, name, active, settings, created_at, updated_at
                    ) VALUES (
                        :id, :slug, 'Audit migration sentinel', true, '{}', now(), now()
                    )
                    """
                ),
                {"id": _ORGANIZATION_ID, "slug": f"audit-migration-{_ORGANIZATION_ID}"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO audit_records (
                        id, organization_id, correlation_id, actor_type, actor_id,
                        action, resource_type, resource_id, outcome, risk_level,
                        policy_decision_id, approval_id, redacted_metadata, latency_ms, created_at
                    ) VALUES (
                        :id, :organization_id, :correlation_id, 'SYSTEM', NULL,
                        'phase1.audit_migration', 'migration_test', :resource_id, 'SUCCESS', NULL,
                        NULL, NULL, '{"sentinel": true}', 7, now()
                    )
                    """
                ),
                {
                    "id": _AUDIT_ID,
                    "organization_id": _ORGANIZATION_ID,
                    "correlation_id": _AUDIT_ID,
                    "resource_id": str(_AUDIT_ID),
                },
            )
            snapshot = await _snapshot(connection, "audit_records")
            await _assert_names(connection, old=True)
        return snapshot
    finally:
        await engine.dispose()


async def _assert_phase(
    table: str,
    before: Mapping[str, Any],
    *,
    old: bool,
) -> dict[str, Any]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            snapshot = await _snapshot(connection, table)
            _assert_preserved(before, snapshot, old=old)
            await _assert_names(connection, old=old)
            await _assert_sentinel(connection, table)
            await connection.rollback()
            await _assert_immutable(connection, table)
            return snapshot
    finally:
        await engine.dispose()


async def _snapshot(connection: AsyncConnection, table: str) -> dict[str, Any]:
    table_row = (
        (
            await connection.execute(
                text(
                    """
                SELECT c.oid::bigint AS object_oid, c.reltoastrelid::bigint AS toast_oid
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = :table AND c.relkind = 'r'
                """
                ),
                {"table": table},
            )
        )
        .mappings()
        .one()
    )
    constraints = await _objects_by_name(
        connection,
        """
        SELECT con.conname AS name, con.oid::bigint AS object_oid,
               con.conrelid::bigint AS table_oid,
               con.confrelid::bigint AS referenced_table_oid,
               con.conindid::bigint AS backing_index_oid,
               con.contype::text AS kind
        FROM pg_constraint con
        WHERE con.conrelid = to_regclass(:qualified_table)
        """,
        table,
    )
    indexes = await _objects_by_name(
        connection,
        """
        SELECT ic.relname AS name, i.indexrelid::bigint AS object_oid,
               i.indrelid::bigint AS table_oid, i.indisprimary,
               i.indisunique, i.indkey::text AS column_numbers
        FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE i.indrelid = to_regclass(:qualified_table)
        """,
        table,
    )
    triggers = await _objects_by_name(
        connection,
        """
        SELECT t.tgname AS name, t.oid::bigint AS object_oid,
               t.tgrelid::bigint AS table_oid, t.tgfoid::bigint AS function_oid,
               t.tgenabled::text AS enabled
        FROM pg_trigger t
        WHERE NOT t.tgisinternal AND t.tgrelid = to_regclass(:qualified_table)
        """,
        table,
    )
    return {
        "table": dict(table_row),
        "constraints": constraints,
        "indexes": indexes,
        "triggers": triggers,
    }


async def _objects_by_name(
    connection: AsyncConnection,
    statement: str,
    table: str,
) -> dict[str, dict[str, Any]]:
    rows = (
        await connection.execute(
            text(statement),
            {"qualified_table": f"public.{table}"},
        )
    ).mappings()
    return {str(row["name"]): dict(row) for row in rows}


def _assert_preserved(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    old: bool,
) -> None:
    assert after["table"] == before["table"]
    _assert_renamed_objects(before["constraints"], after["constraints"], _CONSTRAINT_RENAMES, old)
    _assert_renamed_objects(before["indexes"], after["indexes"], _INDEX_RENAMES, old)
    _assert_renamed_objects(before["triggers"], after["triggers"], _TRIGGER_RENAMES, old)


def _assert_renamed_objects(
    before: Mapping[str, dict[str, Any]],
    after: Mapping[str, dict[str, Any]],
    renames: Mapping[str, str],
    old: bool,
) -> None:
    for old_name, new_name in renames.items():
        expected_name = old_name if old else new_name
        expected = {**before[old_name], "name": expected_name}
        assert after[expected_name] == expected


async def _assert_names(connection: AsyncConnection, *, old: bool) -> None:
    old_table = await connection.scalar(text("SELECT to_regclass('public.audit_records')"))
    new_table = await connection.scalar(text("SELECT to_regclass('public.audit_logs')"))
    expected = (True, False) if old else (False, True)
    assert (old_table is not None, new_table is not None) == expected


async def _assert_sentinel(connection: AsyncConnection, table: str) -> None:
    row = (
        (
            await connection.execute(
                text(
                    f"SELECT id, organization_id, action, redacted_metadata "  # noqa: S608
                    f'FROM "{table}" WHERE id = :id'
                ),
                {"id": _AUDIT_ID},
            )
        )
        .mappings()
        .one()
    )
    assert row["id"] == _AUDIT_ID
    assert row["organization_id"] == _ORGANIZATION_ID
    assert row["action"] == "phase1.audit_migration"
    assert row["redacted_metadata"] == {"sentinel": True}
    count = await connection.scalar(
        text(f'SELECT count(*) FROM "{table}" WHERE id = :id'),  # noqa: S608
        {"id": _AUDIT_ID},
    )
    assert count == 1


async def _assert_immutable(connection: AsyncConnection, table: str) -> None:
    await _assert_mutation_rejected(
        connection,
        text(f'UPDATE "{table}" SET action = :action WHERE id = :id'),  # noqa: S608
        {"action": "mutated", "id": _AUDIT_ID},
    )
    await _assert_mutation_rejected(
        connection,
        text(f'DELETE FROM "{table}" WHERE id = :id'),  # noqa: S608
        {"id": _AUDIT_ID},
    )


async def _assert_mutation_rejected(
    connection: AsyncConnection,
    statement: Any,
    parameters: Mapping[str, Any],
) -> None:
    transaction = await connection.begin()
    try:
        with pytest.raises(DBAPIError) as mutation_error:
            await connection.execute(statement, parameters)
        assert getattr(mutation_error.value.orig, "sqlstate", None) == "23000"
    finally:
        await transaction.rollback()
