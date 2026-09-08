"""add semantic layer tables

Revision ID: f3d4e5a6b7c8
Revises: e8b1c4d7f2a0
Create Date: 2026-09-05 01:30:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f3d4e5a6b7c8"
down_revision = "e8b1c4d7f2a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add semantic layer tables for Phase 102 Data Intelligence Enhancement

    注意: metrics, dimensions, data_sources 表已存在
    这里只添加 entity_definitions 和 query_history
    """

    # Entity Definitions (新增)
    op.create_table(
        "entity_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("primary_table", sa.String(100), nullable=False),
        sa.Column("primary_key", sa.String(100), nullable=False),
        sa.Column("related_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entity_definitions"),
    )
    op.create_index(
        "ix_entity_definitions_organization_id", "entity_definitions", ["organization_id"]
    )
    op.create_index("ix_entity_definitions_name", "entity_definitions", ["organization_id", "name"])

    # Query History (新增 - for learning and optimization)
    op.create_table(
        "query_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("understanding", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("logical_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("compiled_sql", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("rows_returned", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_query_history"),
    )
    op.create_index("ix_query_history_organization_id", "query_history", ["organization_id"])
    op.create_index("ix_query_history_user_id", "query_history", ["user_id"])
    op.create_index("ix_query_history_created_at", "query_history", ["created_at"])


def downgrade() -> None:
    """Remove semantic layer tables"""
    op.drop_table("query_history")
    op.drop_table("entity_definitions")
