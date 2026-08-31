"""add IM principal bindings

Revision ID: e31b7c2d8a01
Revises: d27a8c1e4f90
Create Date: 2026-08-29 13:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e31b7c2d8a01"
down_revision: str | None = "d27a8c1e4f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_principal_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("sender_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("length(trim(channel)) > 0", name="nonempty_im_binding_channel"),
        sa.CheckConstraint("length(trim(sender_id)) > 0", name="nonempty_im_binding_sender_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_principal_bindings_org_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_principal_bindings_org_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "channel",
            "sender_id",
            name="uq_im_principal_bindings_org_channel_sender",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_im_principal_bindings_organization_id_id",
        ),
    )
    op.create_index(
        op.f("ix_im_principal_bindings_organization_id"),
        "im_principal_bindings",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_principal_bindings_user_id"),
        "im_principal_bindings",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_im_principal_bindings_user_id"), table_name="im_principal_bindings")
    op.drop_index(
        op.f("ix_im_principal_bindings_organization_id"),
        table_name="im_principal_bindings",
    )
    op.drop_table("im_principal_bindings")
