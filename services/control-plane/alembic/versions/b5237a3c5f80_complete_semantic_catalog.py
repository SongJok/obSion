"""complete semantic catalog

Revision ID: b5237a3c5f80
Revises: a8ec3bb0de71
Create Date: 2026-08-24 21:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5237a3c5f80"
down_revision: str | None = "a8ec3bb0de71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_synonyms",
        sa.Column("term", sa.String(length=300), nullable=False),
        sa.Column("locale", sa.String(length=40), nullable=False),
        sa.Column("target_type", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
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
            "target_type IN ('METRIC', 'DIMENSION', 'ENTITY', 'RULE')",
            name=op.f("ck_semantic_synonyms_valid_target_type"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_semantic_synonyms_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_synonyms")),
        sa.UniqueConstraint(
            "organization_id",
            "locale",
            "term",
            "target_type",
            "target_id",
            name=op.f("uq_semantic_synonyms_organization_id"),
        ),
    )
    op.create_index(
        op.f("ix_semantic_synonyms_organization_id"),
        "semantic_synonyms",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_semantic_synonyms_target_id"),
        "semantic_synonyms",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "time_definitions",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=120), nullable=False),
        sa.Column("grains", sa.JSON(), nullable=False),
        sa.Column("fiscal_calendar", sa.JSON(), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_time_definitions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_definitions")),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            "version",
            name=op.f("uq_time_definitions_organization_id"),
        ),
    )
    op.create_index(
        op.f("ix_time_definitions_organization_id"),
        "time_definitions",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_time_definitions_organization_id"), table_name="time_definitions")
    op.drop_table("time_definitions")
    op.drop_index(op.f("ix_semantic_synonyms_target_id"), table_name="semantic_synonyms")
    op.drop_index(op.f("ix_semantic_synonyms_organization_id"), table_name="semantic_synonyms")
    op.drop_table("semantic_synonyms")
