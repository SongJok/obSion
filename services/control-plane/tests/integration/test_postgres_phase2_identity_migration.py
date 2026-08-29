from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings
from obsion.domain.enums import SystemRole

_REPOSITORY_ROOT = Path(__file__).parents[4]
_PREVIOUS_REVISION = "f7a1b2c3d4e5"
_PHASE2_REVISION = "8d3f2a1c7b90"
_ORG_A = UUID("018f47ca-4a8c-7df5-9ad3-410000000001")
_ORG_B = UUID("018f47ca-4a8c-7df5-9ad3-420000000001")
_USER_A = UUID("018f47ca-4a8c-7df5-9ad3-410000000002")
_USER_B = UUID("018f47ca-4a8c-7df5-9ad3-420000000002")
_ROLE_A = UUID("018f47ca-4a8c-7df5-9ad3-410000000003")
_ROLE_B = UUID("018f47ca-4a8c-7df5-9ad3-420000000003")
_WORKSPACE_A = UUID("018f47ca-4a8c-7df5-9ad3-410000000004")


def test_phase2_migration_backfills_identity_and_enforces_tenant_constraints() -> None:
    if os.getenv("OBSION_RUN_PHASE2_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL Phase 2 migration test is opt-in")

    config = _alembic_config()
    command.upgrade(config, _PREVIOUS_REVISION)
    asyncio.run(_insert_phase1_identity())

    command.upgrade(config, _PHASE2_REVISION)
    first = asyncio.run(_assert_phase2())
    asyncio.run(_assert_cross_tenant_writes_fail())

    command.downgrade(config, _PREVIOUS_REVISION)
    asyncio.run(_assert_phase1_shape_is_restored())

    command.upgrade(config, _PHASE2_REVISION)
    assert asyncio.run(_assert_phase2()) == first


def _alembic_config() -> Config:
    config = Config(str(_REPOSITORY_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


async def _insert_phase1_identity() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO organizations (
                        id, slug, name, active, settings, created_at, updated_at
                    ) VALUES
                        (:org_a, 'phase2-a', 'Phase 2 A', true, '{}', now(), now()),
                        (:org_b, 'phase2-b', 'Phase 2 B', true, '{}', now(), now())
                    """
                ),
                {"org_a": _ORG_A, "org_b": _ORG_B},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, organization_id, external_id, email, display_name, department,
                        active, attributes, created_at, updated_at
                    ) VALUES
                        (
                            :user_a, :org_a, 'phase2-a', 'a@example.test', 'User A',
                            'Engineering', true, '{}', now(), now()
                        ),
                        (
                            :user_b, :org_b, 'phase2-b', 'b@example.test', 'User B',
                            'Support', true, '{}', now(), now()
                        )
                    """
                ),
                {
                    "user_a": _USER_A,
                    "user_b": _USER_B,
                    "org_a": _ORG_A,
                    "org_b": _ORG_B,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO roles (
                        id, organization_id, name, description, permissions, system,
                        created_at, updated_at
                    ) VALUES
                        (:role_a, :org_a, 'custom-a', '', '["memory.read"]', false, now(), now()),
                        (:role_b, :org_b, 'custom-b', '', '["memory.read"]', false, now(), now())
                    """
                ),
                {
                    "role_a": _ROLE_A,
                    "role_b": _ROLE_B,
                    "org_a": _ORG_A,
                    "org_b": _ORG_B,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO user_roles (
                        user_id, role_id, scope, organization_id, created_at, updated_at
                    ) VALUES (:user_id, :role_id, '{}', :organization_id, now(), now())
                    """
                ),
                {"user_id": _USER_A, "role_id": _ROLE_A, "organization_id": _ORG_A},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO workspaces (
                        id, organization_id, name, description, owner_id, classification,
                        visibility, archived_at, created_at, updated_at
                    ) VALUES (
                        :id, :organization_id, 'Tenant A', '', :owner_id, 'INTERNAL',
                        'PRIVATE', NULL, now(), now()
                    )
                    """
                ),
                {"id": _WORKSPACE_A, "organization_id": _ORG_A, "owner_id": _USER_A},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO workspace_members (
                        workspace_id, user_id, permissions, can_write, created_by,
                        created_at, organization_id
                    ) VALUES (
                        :workspace_id, :user_id, '["read"]', false, :created_by,
                        now(), :organization_id
                    )
                    """
                ),
                {
                    "workspace_id": _WORKSPACE_A,
                    "user_id": _USER_A,
                    "created_by": _USER_A,
                    "organization_id": _ORG_A,
                },
            )
    finally:
        await engine.dispose()


async def _assert_phase2() -> dict[str, object]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            departments = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT u.organization_id, u.department_id, d.name
                            FROM users u
                            JOIN departments d
                              ON d.organization_id = u.organization_id
                             AND d.id = u.department_id
                            WHERE u.id IN (:user_a, :user_b)
                            ORDER BY d.name
                            """
                        ),
                        {"user_a": _USER_A, "user_b": _USER_B},
                    )
                ).tuples()
            )
            system_roles = set(
                await connection.scalars(
                    text(
                        """
                        SELECT name FROM roles
                        WHERE organization_id = :organization_id AND system = true
                        """
                    ),
                    {"organization_id": _ORG_A},
                )
            )
            constraints = set(
                await connection.scalars(
                    text(
                        """
                        SELECT conname FROM pg_constraint
                        WHERE conname LIKE 'fk_%_org_%'
                        """
                    )
                )
            )
    finally:
        await engine.dispose()

    assert [(row[0], row[2]) for row in departments] == [
        (_ORG_A, "Engineering"),
        (_ORG_B, "Support"),
    ]
    assert all(row[1] is not None for row in departments)
    assert system_roles >= {role.value for role in SystemRole}
    expected_constraints = {
        "fk_users_org_department",
        "fk_user_roles_org_user",
        "fk_user_roles_org_role",
        "fk_workspaces_org_owner",
        "fk_workspace_members_org_workspace",
        "fk_workspace_members_org_user",
        "fk_workspace_members_org_creator",
    }
    assert constraints >= expected_constraints
    return {
        "departments": [(str(row[0]), str(row[1]), row[2]) for row in departments],
        "system_roles": sorted(system_roles),
        "constraints": sorted(constraints & expected_constraints),
    }


async def _assert_cross_tenant_writes_fail() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO user_roles (
                            user_id, role_id, scope, organization_id, created_at, updated_at
                        ) VALUES (:user_id, :role_id, '{}', :organization_id, now(), now())
                        """
                    ),
                    {"user_id": _USER_A, "role_id": _ROLE_B, "organization_id": _ORG_A},
                )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        INSERT INTO workspaces (
                            id, organization_id, name, description, owner_id, classification,
                            visibility, archived_at, created_at, updated_at
                        ) VALUES (
                            '018f47ca-4a8c-7df5-9ad3-410000000099', :organization_id,
                            'Invalid owner', '', :owner_id, 'INTERNAL', 'PRIVATE',
                            NULL, now(), now()
                        )
                        """
                    ),
                    {"organization_id": _ORG_A, "owner_id": _USER_B},
                )
    finally:
        await engine.dispose()


async def _assert_phase1_shape_is_restored() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("SELECT department FROM users WHERE id = :id"),
                        {"id": _USER_A},
                    )
                )
                .tuples()
                .one()
            )
            departments = await connection.scalar(text("SELECT to_regclass('departments')"))
            old_constraints = set(
                await connection.scalars(
                    text(
                        """
                        SELECT conname FROM pg_constraint
                        WHERE conname IN (
                            'fk_user_roles_user_id_users',
                            'fk_user_roles_role_id_roles',
                            'fk_workspaces_owner_id_users',
                            'fk_workspace_members_workspace_id_workspaces',
                            'fk_workspace_members_user_id_users',
                            'fk_workspace_members_created_by_users'
                        )
                        """
                    )
                )
            )
    finally:
        await engine.dispose()

    assert row[0] == "Engineering"
    assert departments is None
    assert len(old_constraints) == 6
