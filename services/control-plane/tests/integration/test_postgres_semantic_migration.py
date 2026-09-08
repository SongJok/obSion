from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

import obsion.config as obsion_config
from obsion.config import Settings

_REPOSITORY_ROOT = Path(__file__).parents[4]
_PREVIOUS_REVISION = "e8b1c4d7f2a0"
_SEMANTIC_REVISION = "f3d4e5a6b7c8"

_ORGANIZATION_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000001")
_ENTITY_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000002")
_QUERY_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000003")
_USER_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000004")
_RUN_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000005")
_CONNECTOR_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000006")
_DATA_SOURCE_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000007")
_DATA_TABLE_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000008")
_METRIC_ID = UUID("018f47ca-4a8c-7df5-9ad3-410000000009")


def _settings() -> Settings:
    return Settings(_env_file=None)


def _engine() -> AsyncEngine:
    return create_async_engine(_settings().database_url, poolclass=NullPool)


_EXPECTED_SCHEMA = {
    "entity_definitions": {
        "columns": [
            ("id", "uuid", True, None),
            ("organization_id", "uuid", True, None),
            ("name", "character varying(200)", True, None),
            ("display_name", "character varying(200)", True, None),
            ("description", "text", False, None),
            ("primary_table", "character varying(100)", True, None),
            ("primary_key", "character varying(100)", True, None),
            ("related_tables", "jsonb", False, None),
            ("metadata", "jsonb", False, None),
            ("active", "boolean", True, "true"),
            ("created_at", "timestamp with time zone", True, "now()"),
            ("updated_at", "timestamp with time zone", True, "now()"),
        ],
        "constraints": [("pk_entity_definitions", "p")],
        "indexes": [
            (
                "ix_entity_definitions_name",
                False,
                False,
                ("organization_id", "name"),
            ),
            (
                "ix_entity_definitions_organization_id",
                False,
                False,
                ("organization_id",),
            ),
            ("pk_entity_definitions", True, True, ("id",)),
        ],
    },
    "query_history": {
        "columns": [
            ("id", "uuid", True, None),
            ("organization_id", "uuid", True, None),
            ("user_id", "uuid", True, None),
            ("run_id", "uuid", False, None),
            ("question", "text", True, None),
            ("understanding", "jsonb", False, None),
            ("logical_plan", "jsonb", False, None),
            ("compiled_sql", "text", False, None),
            ("execution_time_ms", "integer", False, None),
            ("rows_returned", "integer", False, None),
            ("success", "boolean", True, None),
            ("error_message", "text", False, None),
            ("created_at", "timestamp with time zone", True, "now()"),
        ],
        "constraints": [("pk_query_history", "p")],
        "indexes": [
            ("ix_query_history_created_at", False, False, ("created_at",)),
            (
                "ix_query_history_organization_id",
                False,
                False,
                ("organization_id",),
            ),
            ("ix_query_history_user_id", False, False, ("user_id",)),
            ("pk_query_history", True, True, ("id",)),
        ],
    },
}


def test_semantic_migration_preserves_existing_rows_and_round_trips_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("OBSION_RUN_SEMANTIC_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL semantic migration test is opt-in")

    settings = _settings()
    monkeypatch.setattr(obsion_config, "get_settings", lambda: settings)
    config = _alembic_config(settings.database_url)
    command.upgrade(config, _PREVIOUS_REVISION)
    assert asyncio.run(_table_presence()) == {
        "entity_definitions": False,
        "query_history": False,
    }
    existing_semantic_sentinel = asyncio.run(_insert_existing_semantic_sentinel())

    command.upgrade(config, _SEMANTIC_REVISION)
    first_schema = asyncio.run(_schema_snapshot())
    assert first_schema == _EXPECTED_SCHEMA
    assert asyncio.run(_existing_semantic_sentinel()) == existing_semantic_sentinel
    legacy_sentinel = asyncio.run(_insert_and_snapshot_legacy_sentinel())

    # 这些生产环境中的空操作模拟已升级数据库。兼容映射必须阻止 Alembic
    # 删除任一历史表或行，同时既有语义目录 sentinel 也必须保持不变。
    for _ in range(2):
        command.upgrade(config, "head")
        command.check(config)
        assert asyncio.run(_schema_snapshot()) == first_schema
        assert asyncio.run(_existing_semantic_sentinel()) == existing_semantic_sentinel
        assert asyncio.run(_legacy_sentinel_snapshot()) == legacy_sentinel

    # f3 降级会有意删除两个历史表。本往返只证明迁移能重建相同结构，
    # 不证明已删除表内的数据能在降级后保留。
    command.downgrade(config, _PREVIOUS_REVISION)
    assert asyncio.run(_table_presence()) == {
        "entity_definitions": False,
        "query_history": False,
    }
    command.upgrade(config, _SEMANTIC_REVISION)
    assert asyncio.run(_schema_snapshot()) == first_schema


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_REPOSITORY_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option(
        "script_location",
        str(_REPOSITORY_ROOT / "services/control-plane/alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def _table_presence() -> dict[str, bool]:
    engine = _engine()
    try:
        async with engine.connect() as connection:
            return {
                table: bool(
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_table) IS NOT NULL"),
                        {"qualified_table": f"public.{table}"},
                    )
                )
                for table in _EXPECTED_SCHEMA
            }
    finally:
        await engine.dispose()


async def _schema_snapshot() -> dict[str, Any]:
    engine = _engine()
    try:
        async with engine.connect() as connection:
            return {table: await _table_schema(connection, table) for table in _EXPECTED_SCHEMA}
    finally:
        await engine.dispose()


async def _table_schema(connection: AsyncConnection, table: str) -> dict[str, Any]:
    columns = list(
        (
            await connection.execute(
                text(
                    """
                    SELECT attribute.attname,
                           format_type(attribute.atttypid, attribute.atttypmod),
                           attribute.attnotnull,
                           pg_get_expr(default_value.adbin, default_value.adrelid)
                    FROM pg_attribute AS attribute
                    JOIN pg_class AS table_class
                      ON table_class.oid = attribute.attrelid
                    JOIN pg_namespace AS namespace
                      ON namespace.oid = table_class.relnamespace
                    LEFT JOIN pg_attrdef AS default_value
                      ON default_value.adrelid = attribute.attrelid
                     AND default_value.adnum = attribute.attnum
                    WHERE namespace.nspname = 'public'
                      AND table_class.relname = :table
                      AND attribute.attnum > 0
                      AND NOT attribute.attisdropped
                    ORDER BY attribute.attnum
                    """
                ),
                {"table": table},
            )
        ).tuples()
    )
    constraints = list(
        (
            await connection.execute(
                text(
                    """
                    SELECT constraint_data.conname, constraint_data.contype::text
                    FROM pg_constraint AS constraint_data
                    WHERE constraint_data.conrelid = to_regclass(:qualified_table)
                    ORDER BY constraint_data.conname
                    """
                ),
                {"qualified_table": f"public.{table}"},
            )
        ).tuples()
    )
    index_rows = (
        await connection.execute(
            text(
                """
                SELECT index_class.relname,
                       index_data.indisunique,
                       index_data.indisprimary,
                       ARRAY(
                           SELECT pg_get_indexdef(
                               index_data.indexrelid,
                               key_position,
                               true
                           )
                           FROM generate_series(
                               1,
                               index_data.indnkeyatts
                           ) AS key_position
                           ORDER BY key_position
                       )
                FROM pg_index AS index_data
                JOIN pg_class AS index_class
                  ON index_class.oid = index_data.indexrelid
                WHERE index_data.indrelid = to_regclass(:qualified_table)
                ORDER BY index_class.relname
                """
            ),
            {"qualified_table": f"public.{table}"},
        )
    ).tuples()
    indexes = [(name, unique, primary, tuple(keys)) for name, unique, primary, keys in index_rows]
    return {"columns": columns, "constraints": constraints, "indexes": indexes}


async def _insert_existing_semantic_sentinel() -> dict[str, Any]:
    engine = _engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO organizations (
                        id, slug, name, active, settings, created_at, updated_at
                    ) VALUES (
                        :organization_id, 'semantic-migration-sentinel',
                        'Semantic migration sentinel', true, '{}', now(), now()
                    )
                    """
                ),
                {"organization_id": _ORGANIZATION_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO connectors (
                        id, organization_id, name, connector_type, status, environment,
                        endpoint, configuration, credential_ref, declared_grants,
                        allowed_egress, last_health, created_at, updated_at
                    ) VALUES (
                        :connector_id, :organization_id, 'semantic-migration-connector',
                        'POSTGRESQL', 'ACTIVE', 'test', NULL, '{}', NULL, '[]', '[]',
                        '{}', now(), now()
                    )
                    """
                ),
                {"connector_id": _CONNECTOR_ID, "organization_id": _ORGANIZATION_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO data_sources (
                        id, organization_id, name, dialect, connector_id, environment,
                        read_only, classification, query_policy, created_at, updated_at
                    ) VALUES (
                        :data_source_id, :organization_id, 'semantic-migration-source',
                        'postgresql', :connector_id, 'test', true, 'INTERNAL', '{}',
                        now(), now()
                    )
                    """
                ),
                {
                    "data_source_id": _DATA_SOURCE_ID,
                    "organization_id": _ORGANIZATION_ID,
                    "connector_id": _CONNECTOR_ID,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO data_tables (
                        id, organization_id, data_source_id, schema_name, table_name,
                        description, owner, classification, row_policy, created_at, updated_at
                    ) VALUES (
                        :table_id, :organization_id, :data_source_id, 'analytics', 'orders',
                        'semantic migration table sentinel', 'migration-test', 'INTERNAL',
                        '{}', now(), now()
                    )
                    """
                ),
                {
                    "table_id": _DATA_TABLE_ID,
                    "organization_id": _ORGANIZATION_ID,
                    "data_source_id": _DATA_SOURCE_ID,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO metrics (
                        id, organization_id, name, display_name, version, expression,
                        filters, time_column, source_table_id, owner, synonyms, validated,
                        created_at, updated_at
                    ) VALUES (
                        :metric_id, :organization_id, 'semantic_migration_orders',
                        'Semantic migration orders', 1, 'count(*)', '{}', 'created_at',
                        :table_id, 'migration-test', '["orders"]', true, now(), now()
                    )
                    """
                ),
                {
                    "metric_id": _METRIC_ID,
                    "organization_id": _ORGANIZATION_ID,
                    "table_id": _DATA_TABLE_ID,
                },
            )
            return await _read_existing_semantic_sentinel(connection)
    finally:
        await engine.dispose()


async def _existing_semantic_sentinel() -> dict[str, Any]:
    engine = _engine()
    try:
        async with engine.connect() as connection:
            return await _read_existing_semantic_sentinel(connection)
    finally:
        await engine.dispose()


async def _read_existing_semantic_sentinel(connection: AsyncConnection) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    """
                    SELECT metric.id, metric.organization_id, metric.name, metric.version,
                           metric.expression, metric.filters, metric.time_column,
                           metric.source_table_id, metric.owner, metric.synonyms,
                           metric.validated
                    FROM metrics AS metric
                    WHERE metric.id = :metric_id
                    """
                ),
                {"metric_id": _METRIC_ID},
            )
        )
        .mappings()
        .one()
    )
    count = await connection.scalar(
        text("SELECT count(*) FROM metrics WHERE id = :metric_id"),
        {"metric_id": _METRIC_ID},
    )
    assert count == 1
    return dict(row)


async def _insert_and_snapshot_legacy_sentinel() -> dict[str, dict[str, Any]]:
    engine = _engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO entity_definitions (
                        id, organization_id, name, display_name, description,
                        primary_table, primary_key, related_tables, metadata, active
                    ) VALUES (
                        :id, :organization_id, 'migration_sentinel', 'Migration sentinel',
                        'must survive repeated upgrade head and check', 'analytics.orders',
                        'order_id', '["analytics.customers"]'::jsonb,
                        '{"sentinel": true}'::jsonb, true
                    )
                    """
                ),
                {"id": _ENTITY_ID, "organization_id": _ORGANIZATION_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO query_history (
                        id, organization_id, user_id, run_id, question, understanding,
                        logical_plan, compiled_sql, execution_time_ms, rows_returned,
                        success, error_message
                    ) VALUES (
                        :id, :organization_id, :user_id, :run_id,
                        'semantic migration sentinel', '{"intent": "sentinel"}'::jsonb,
                        '{"metric": "orders"}'::jsonb, 'SELECT 1', 7, 1, true, NULL
                    )
                    """
                ),
                {
                    "id": _QUERY_ID,
                    "organization_id": _ORGANIZATION_ID,
                    "user_id": _USER_ID,
                    "run_id": _RUN_ID,
                },
            )
            return await _legacy_sentinel_snapshot(connection)
    finally:
        await engine.dispose()


async def _legacy_sentinel_snapshot(
    connection: AsyncConnection | None = None,
) -> dict[str, dict[str, Any]]:
    if connection is not None:
        return await _read_legacy_sentinel(connection)

    engine = _engine()
    try:
        async with engine.connect() as standalone_connection:
            return await _read_legacy_sentinel(standalone_connection)
    finally:
        await engine.dispose()


async def _read_legacy_sentinel(connection: AsyncConnection) -> dict[str, dict[str, Any]]:
    entity = (
        (
            await connection.execute(
                text("SELECT * FROM entity_definitions WHERE id = :id"),
                {"id": _ENTITY_ID},
            )
        )
        .mappings()
        .one()
    )
    query = (
        (
            await connection.execute(
                text("SELECT * FROM query_history WHERE id = :id"),
                {"id": _QUERY_ID},
            )
        )
        .mappings()
        .one()
    )
    entity_count = await connection.scalar(
        text("SELECT count(*) FROM entity_definitions WHERE id = :id"),
        {"id": _ENTITY_ID},
    )
    query_count = await connection.scalar(
        text("SELECT count(*) FROM query_history WHERE id = :id"),
        {"id": _QUERY_ID},
    )
    assert entity_count == 1
    assert query_count == 1
    return {"entity_definitions": dict(entity), "query_history": dict(query)}
