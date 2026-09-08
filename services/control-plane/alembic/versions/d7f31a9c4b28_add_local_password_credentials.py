"""add local password credentials

Revision ID: d7f31a9c4b28
Revises: b88f1c4d5e60
Create Date: 2026-09-03 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f31a9c4b28"
down_revision: str | None = "b88f1c4d5e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("encoded_secret", sa.String(length=512), nullable=False),
        sa.Column("must_change", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False),
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
            "length(trim(encoded_secret)) > 0",
            name=op.f("ck_user_credentials_nonempty_encoded_secret"),
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name=op.f("ck_user_credentials_nonnegative_failed_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_user_credentials_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_user_credentials_org_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_credentials")),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_user_credentials_org_user",
        ),
    )
    op.create_index(
        op.f("ix_user_credentials_organization_id"),
        "user_credentials",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_credentials_user_id"),
        "user_credentials",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_credentials_user_id"), table_name="user_credentials")
    op.drop_index(op.f("ix_user_credentials_organization_id"), table_name="user_credentials")
    op.drop_table("user_credentials")
