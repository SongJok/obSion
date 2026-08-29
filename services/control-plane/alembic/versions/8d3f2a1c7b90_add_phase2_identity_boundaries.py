"""add phase 2 identity and tenant boundaries

Revision ID: 8d3f2a1c7b90
Revises: f7a1b2c3d4e5
Create Date: 2026-08-27 10:30:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "8d3f2a1c7b90"
down_revision: str | None = "f7a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_NAMESPACE = UUID("7ded2778-24a0-4b70-a8f6-a2f0afe05510")
_SYSTEM_ROLES: tuple[tuple[str, str, list[str]], ...] = (
    (
        "admin",
        "Organization administrator with full control-plane access",
        ["*"],
    ),
    (
        "engineer",
        "Engineering contributor for governed investigation and development workflows",
        [
            "artifact.write",
            "automation.trigger",
            "evaluations.read",
            "evaluations.write",
            "knowledge.read.confidential",
            "knowledge.read.internal",
            "knowledge.write",
            "memory.read",
            "memory.write",
        ],
    ),
    (
        "analyst",
        "Data analyst for governed research, semantic data, and evidence workflows",
        [
            "artifact.write",
            "evaluations.read",
            "knowledge.read.confidential",
            "knowledge.read.internal",
            "memory.read",
            "memory.write",
        ],
    ),
    (
        "operator",
        "Operations responder for governed observability and approval workflows",
        [
            "approval.decide",
            "approval.read",
            "artifact.write",
            "automation.trigger",
            "knowledge.read.confidential",
            "knowledge.read.internal",
            "memory.read",
            "memory.write",
        ],
    ),
    (
        "support",
        "Support investigator with bounded internal knowledge and workspace access",
        [
            "artifact.write",
            "knowledge.read.internal",
            "memory.read",
            "memory.write",
        ],
    ),
    (
        "viewer",
        "Read-only participant for authorized workspaces and internal knowledge",
        ["knowledge.read.internal", "memory.read"],
    ),
)


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(name)) > 0",
            name=op.f("ck_departments_nonempty_department_name"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_departments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "parent_id"],
            ["departments.organization_id", "departments.id"],
            name="fk_departments_org_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("organization_id", "id", name="uq_departments_organization_id_id"),
        sa.UniqueConstraint("organization_id", "name", name="uq_departments_organization_id_name"),
    )
    op.create_index(
        op.f("ix_departments_organization_id"),
        "departments",
        ["organization_id"],
        unique=False,
    )

    op.execute(
        "ALTER TABLE users RENAME CONSTRAINT "
        "uq_users_organization_id TO uq_users_organization_id_external_id"
    )
    op.execute(
        "ALTER TABLE roles RENAME CONSTRAINT "
        "uq_roles_organization_id TO uq_roles_organization_id_name"
    )
    op.create_unique_constraint("uq_users_organization_id_id", "users", ["organization_id", "id"])
    op.create_unique_constraint("uq_roles_organization_id_id", "roles", ["organization_id", "id"])
    op.create_unique_constraint(
        "uq_workspaces_organization_id_id", "workspaces", ["organization_id", "id"]
    )
    op.create_check_constraint(
        op.f("ck_roles_nonempty_role_name"), "roles", "length(trim(name)) > 0"
    )

    op.add_column("users", sa.Column("department_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_users_department_id"), "users", ["department_id"], unique=False)
    op.execute(
        """
        INSERT INTO departments (
            id, organization_id, name, description, parent_id, active, created_at, updated_at
        )
        SELECT
            md5(organization_id::text || E'\\x1f' || trim(department))::uuid,
            organization_id,
            trim(department),
            '',
            NULL,
            true,
            now(),
            now()
        FROM users
        WHERE department IS NOT NULL AND length(trim(department)) > 0
        GROUP BY organization_id, trim(department)
        ON CONFLICT (organization_id, name) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE users AS u
        SET department_id = d.id
        FROM departments AS d
        WHERE d.organization_id = u.organization_id
          AND d.name = trim(u.department)
        """
    )

    _assert_existing_tenant_integrity()

    op.drop_constraint("fk_user_roles_user_id_users", "user_roles", type_="foreignkey")
    op.drop_constraint("fk_user_roles_role_id_roles", "user_roles", type_="foreignkey")
    op.drop_constraint("fk_workspaces_owner_id_users", "workspaces", type_="foreignkey")
    op.drop_constraint(
        "fk_workspace_members_workspace_id_workspaces", "workspace_members", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workspace_members_user_id_users", "workspace_members", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workspace_members_created_by_users", "workspace_members", type_="foreignkey"
    )

    op.create_foreign_key(
        "fk_users_org_department",
        "users",
        "departments",
        ["organization_id", "department_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_user_roles_org_user",
        "user_roles",
        "users",
        ["organization_id", "user_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_user_roles_org_role",
        "user_roles",
        "roles",
        ["organization_id", "role_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspaces_org_owner",
        "workspaces",
        "users",
        ["organization_id", "owner_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_workspace_members_org_workspace",
        "workspace_members",
        "workspaces",
        ["organization_id", "workspace_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspace_members_org_user",
        "workspace_members",
        "users",
        ["organization_id", "user_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspace_members_org_creator",
        "workspace_members",
        "users",
        ["organization_id", "created_by"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_column("users", "department")

    _seed_system_roles()


def downgrade() -> None:
    op.add_column("users", sa.Column("department", sa.String(length=200), nullable=True))
    op.execute(
        """
        UPDATE users AS u
        SET department = d.name
        FROM departments AS d
        WHERE d.organization_id = u.organization_id
          AND d.id = u.department_id
        """
    )

    op.drop_constraint("fk_workspace_members_org_creator", "workspace_members", type_="foreignkey")
    op.drop_constraint("fk_workspace_members_org_user", "workspace_members", type_="foreignkey")
    op.drop_constraint(
        "fk_workspace_members_org_workspace", "workspace_members", type_="foreignkey"
    )
    op.drop_constraint("fk_workspaces_org_owner", "workspaces", type_="foreignkey")
    op.drop_constraint("fk_user_roles_org_role", "user_roles", type_="foreignkey")
    op.drop_constraint("fk_user_roles_org_user", "user_roles", type_="foreignkey")
    op.drop_constraint("fk_users_org_department", "users", type_="foreignkey")

    op.create_foreign_key(
        "fk_user_roles_user_id_users",
        "user_roles",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_user_roles_role_id_roles",
        "user_roles",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspaces_owner_id_users", "workspaces", "users", ["owner_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_workspace_members_workspace_id_workspaces",
        "workspace_members",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspace_members_user_id_users",
        "workspace_members",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workspace_members_created_by_users",
        "workspace_members",
        "users",
        ["created_by"],
        ["id"],
    )

    op.drop_index(op.f("ix_users_department_id"), table_name="users")
    op.drop_column("users", "department_id")
    op.drop_index(op.f("ix_departments_organization_id"), table_name="departments")
    op.drop_table("departments")

    op.drop_constraint(op.f("ck_roles_nonempty_role_name"), "roles", type_="check")
    op.drop_constraint("uq_workspaces_organization_id_id", "workspaces", type_="unique")
    op.drop_constraint("uq_roles_organization_id_id", "roles", type_="unique")
    op.drop_constraint("uq_users_organization_id_id", "users", type_="unique")
    op.execute(
        "ALTER TABLE roles RENAME CONSTRAINT "
        "uq_roles_organization_id_name TO uq_roles_organization_id"
    )
    op.execute(
        "ALTER TABLE users RENAME CONSTRAINT "
        "uq_users_organization_id_external_id TO uq_users_organization_id"
    )


def _assert_existing_tenant_integrity() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM user_roles ur
            JOIN users u ON u.id = ur.user_id
            WHERE u.organization_id <> ur.organization_id
          ) THEN
            RAISE EXCEPTION 'Phase 2 migration refused cross-organization user role binding';
          END IF;
          IF EXISTS (
            SELECT 1 FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE r.organization_id <> ur.organization_id
          ) THEN
            RAISE EXCEPTION 'Phase 2 migration refused cross-organization role binding';
          END IF;
          IF EXISTS (
            SELECT 1 FROM workspaces w
            JOIN users u ON u.id = w.owner_id
            WHERE u.organization_id <> w.organization_id
          ) THEN
            RAISE EXCEPTION 'Phase 2 migration refused cross-organization workspace owner';
          END IF;
          IF EXISTS (
            SELECT 1 FROM workspace_members wm
            JOIN workspaces w ON w.id = wm.workspace_id
            WHERE w.organization_id <> wm.organization_id
          ) OR EXISTS (
            SELECT 1 FROM workspace_members wm
            JOIN users u ON u.id = wm.user_id
            WHERE u.organization_id <> wm.organization_id
          ) OR EXISTS (
            SELECT 1 FROM workspace_members wm
            JOIN users u ON u.id = wm.created_by
            WHERE u.organization_id <> wm.organization_id
          ) THEN
            RAISE EXCEPTION 'Phase 2 migration refused cross-organization workspace membership';
          END IF;
        END
        $$
        """
    )


def _seed_system_roles() -> None:
    bind = op.get_bind()
    role_table = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("organization_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("permissions", sa.JSON()),
        sa.column("system", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    organization_ids = list(bind.scalars(sa.text("SELECT id FROM organizations")))
    required_names = {name for name, _, _ in _SYSTEM_ROLES}
    now = datetime.now(UTC)
    for organization_id in organization_ids:
        existing = {
            str(row.name): bool(row.system)
            for row in bind.execute(
                sa.text("SELECT name, system FROM roles WHERE organization_id = :organization_id"),
                {"organization_id": organization_id},
            )
        }
        conflicts = sorted(
            name for name in required_names if name in existing and not existing[name]
        )
        if conflicts:
            names = ", ".join(conflicts)
            raise RuntimeError(
                f"custom roles shadow reserved Phase 2 system roles for {organization_id}: {names}"
            )
        for name, description, permissions in _SYSTEM_ROLES:
            if name in existing:
                bind.execute(
                    role_table.update()
                    .where(role_table.c.organization_id == organization_id)
                    .where(role_table.c.name == name)
                    .values(
                        description=description,
                        permissions=permissions,
                        system=True,
                        updated_at=now,
                    )
                )
        rows = [
            {
                "id": uuid5(_ROLE_NAMESPACE, f"{organization_id}:{name}"),
                "organization_id": organization_id,
                "name": name,
                "description": description,
                "permissions": permissions,
                "system": True,
                "created_at": now,
                "updated_at": now,
            }
            for name, description, permissions in _SYSTEM_ROLES
            if name not in existing
        ]
        if rows:
            bind.execute(role_table.insert(), rows)
