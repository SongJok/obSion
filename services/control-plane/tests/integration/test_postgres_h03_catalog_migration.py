from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import obsion.config as obsion_config
from obsion.config import Settings

_ROOT = Path(__file__).resolve().parents[4]
_PREVIOUS = "c5d7e9f1a3b5"
_REVISION = "d6e8f0a2b4c6"
_ORG_ID = UUID("018f47ca-4a8c-7df5-9ad3-430000000001")
_ROLE_ID = UUID("018f47ca-4a8c-7df5-9ad3-430000000002")
_RESOURCE_ID = UUID("018f47ca-4a8c-7df5-9ad3-430000000003")


def test_h03_catalog_migration_round_trip_role_grant_and_nonempty_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("OBSION_RUN_H03_CATALOG_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL H03 catalog migration test is opt-in")
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql+asyncpg://")
    monkeypatch.setattr(obsion_config, "get_settings", lambda: settings)
    config = Config(str(_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "services/control-plane/alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)

    command.upgrade(config, _PREVIOUS)
    asyncio.run(_insert_existing_system_role(settings.database_url))
    command.upgrade(config, _REVISION)
    command.check(config)
    assert asyncio.run(_snapshot(settings.database_url)) == {
        "catalog_resources": True,
        "catalog_relations": True,
        "permissions": ["memory.read", "catalog.discover"],
    }

    command.downgrade(config, _PREVIOUS)
    assert asyncio.run(_snapshot(settings.database_url)) == {
        "catalog_resources": False,
        "catalog_relations": False,
        "permissions": ["memory.read", "catalog.discover"],
    }
    command.upgrade(config, _REVISION)
    command.check(config)
    asyncio.run(_insert_catalog_fact(settings.database_url))

    with pytest.raises(RuntimeError, match="populated enterprise catalog"):
        command.downgrade(config, _PREVIOUS)
    assert asyncio.run(_current_revision(settings.database_url)) == _REVISION
    command.check(config)


async def _insert_existing_system_role(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO organizations (
                        id, slug, name, active, settings, created_at, updated_at
                    ) VALUES (
                        :org_id, 'h03-catalog-migration', 'H03 migration tenant',
                        true, '{}', now(), now()
                    )
                    """
                ),
                {"org_id": _ORG_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO roles (
                        id, organization_id, name, description, permissions,
                        system, created_at, updated_at
                    ) VALUES (
                        :role_id, :org_id, 'viewer', 'Existing viewer',
                        '["memory.read"]', true, now(), now()
                    )
                    """
                ),
                {"role_id": _ROLE_ID, "org_id": _ORG_ID},
            )
    finally:
        await engine.dispose()


async def _snapshot(url: str) -> dict[str, object]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            resources = bool(
                await connection.scalar(text("SELECT to_regclass('public.catalog_resources')"))
            )
            relations = bool(
                await connection.scalar(text("SELECT to_regclass('public.catalog_relations')"))
            )
            permissions = await connection.scalar(
                text("SELECT permissions FROM roles WHERE id = :role_id"),
                {"role_id": _ROLE_ID},
            )
            return {
                "catalog_resources": resources,
                "catalog_relations": relations,
                "permissions": permissions,
            }
    finally:
        await engine.dispose()


async def _insert_catalog_fact(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO catalog_resources (
                        id, organization_id, kind, canonical_key, display_name,
                        description, owner, version, state, required_permission,
                        classification, access_policy, search_terms, source_type,
                        source_ref, source_version, verification_level, observed_at,
                        valid_from, valid_until, created_at, updated_at
                    ) VALUES (
                        :id, :org_id, 'SERVICE', 'service:migration-sentinel',
                        'Migration sentinel', '', NULL, 'v1', 'ACTIVE',
                        'catalog.discover', 'INTERNAL', '{}', '[]', 'MIGRATION_TEST',
                        'sentinel', 'v1', 'DECLARED', now(), now(), NULL, now(), now()
                    )
                    """
                ),
                {"id": _RESOURCE_ID, "org_id": _ORG_ID},
            )
    finally:
        await engine.dispose()


async def _current_revision(url: str) -> str:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert isinstance(value, str)
            return value
    finally:
        await engine.dispose()
