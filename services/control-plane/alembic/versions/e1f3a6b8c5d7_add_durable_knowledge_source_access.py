"""add durable knowledge source access

Revision ID: e1f3a6b8c5d7
Revises: d0e2f5a7b3c4
Create Date: 2026-09-11 21:29:45.265537
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f3a6b8c5d7"
down_revision: str | None = "d0e2f5a7b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_documents_org_id", "documents", ["organization_id", "id"])
    op.create_table(
        "knowledge_sync_sources",
        sa.Column("connector_version_id", sa.Uuid(), nullable=False),
        sa.Column("principal_binding_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("corp_id", sa.String(length=128), nullable=False),
        sa.Column("app_key", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("scan_state", sa.JSON(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
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
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name=op.f("ck_knowledge_sync_sources_paired_lease"),
        ),
        sa.CheckConstraint(
            "generation >= 0", name=op.f("ck_knowledge_sync_sources_nonnegative_generation")
        ),
        sa.CheckConstraint(
            "length(trim(corp_id)) > 0", name=op.f("ck_knowledge_sync_sources_nonempty_corp_id")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "connector_version_id"],
            [
                "connector_configuration_versions.organization_id",
                "connector_configuration_versions.id",
            ],
            name="fk_knowledge_sync_sources_connector_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "principal_binding_id"],
            ["im_principal_bindings.organization_id", "im_principal_bindings.id"],
            name="fk_knowledge_sync_sources_principal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_knowledge_sync_sources_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_knowledge_sync_sources_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sync_sources")),
        sa.UniqueConstraint(
            "organization_id",
            "connector_version_id",
            "principal_binding_id",
            name="uq_knowledge_sync_sources_binding",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_knowledge_sync_sources_org_id"),
    )
    op.create_index(
        op.f("ix_knowledge_sync_sources_next_poll_at"),
        "knowledge_sync_sources",
        ["next_poll_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_sync_sources_organization_id"),
        "knowledge_sync_sources",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_sync_items",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("seen_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("source_revision", sa.String(length=200), nullable=True),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
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
            "status != 'READY' OR (document_id IS NOT NULL AND checked_at IS NOT NULL "
            "AND access_expires_at IS NOT NULL)",
            name=op.f("ck_knowledge_sync_items_ready_has_access_lease"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'READY', 'PARTIAL', 'DENIED', 'MISSING', 'FAILED')",
            name=op.f("ck_knowledge_sync_items_valid_status"),
        ),
        sa.CheckConstraint(
            "access_expires_at IS NULL OR (checked_at IS NOT NULL "
            "AND access_expires_at > checked_at)",
            name=op.f("ck_knowledge_sync_items_ordered_access_lease"),
        ),
        sa.CheckConstraint(
            "seen_generation >= 0", name=op.f("ck_knowledge_sync_items_nonnegative_generation")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            name="fk_knowledge_sync_items_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["knowledge_sync_sources.organization_id", "knowledge_sync_sources.id"],
            name="fk_knowledge_sync_items_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_knowledge_sync_items_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sync_items")),
        sa.UniqueConstraint("source_id", "node_id", name="uq_knowledge_sync_items_node"),
    )
    op.create_index(
        op.f("ix_knowledge_sync_items_access_expires_at"),
        "knowledge_sync_items",
        ["access_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_sync_items_document_id"),
        "knowledge_sync_items",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_sync_items_organization_id"),
        "knowledge_sync_items",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_knowledge_sync_items_organization_id"), table_name="knowledge_sync_items"
    )
    op.drop_index(op.f("ix_knowledge_sync_items_document_id"), table_name="knowledge_sync_items")
    op.drop_index(
        op.f("ix_knowledge_sync_items_access_expires_at"), table_name="knowledge_sync_items"
    )
    op.drop_table("knowledge_sync_items")
    op.drop_index(
        op.f("ix_knowledge_sync_sources_organization_id"), table_name="knowledge_sync_sources"
    )
    op.drop_index(
        op.f("ix_knowledge_sync_sources_next_poll_at"), table_name="knowledge_sync_sources"
    )
    op.drop_table("knowledge_sync_sources")
    op.drop_constraint("uq_documents_org_id", "documents", type_="unique")
