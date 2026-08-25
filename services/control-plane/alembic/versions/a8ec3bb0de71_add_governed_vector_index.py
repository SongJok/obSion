"""add governed vector index

Revision ID: a8ec3bb0de71
Revises: c93717e47198
Create Date: 2026-08-24 19:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision: str = "a8ec3bb0de71"
down_revision: str | None = "c93717e47198"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.alter_column("model_calls", "run_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "model_calls",
        sa.Column("operation", sa.String(length=40), server_default="CHAT", nullable=False),
    )
    op.alter_column(
        "model_calls", "operation", existing_type=sa.String(length=40), server_default=None
    )
    op.add_column("document_chunks", sa.Column("embedding", VECTOR(dim=1536), nullable=True))
    op.create_table(
        "document_chunk_grants",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("effect", sa.String(length=12), nullable=False),
        sa.Column("subject_type", sa.String(length=24), nullable=False),
        sa.Column("subject_value", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "effect IN ('ALLOW', 'DENY')",
            name=op.f("ck_document_chunk_grants_valid_effect"),
        ),
        sa.CheckConstraint(
            "subject_type IN ('USER', 'ROLE', 'DEPARTMENT', 'ORGANIZATION')",
            name=op.f("ck_document_chunk_grants_valid_subject_type"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_document_chunk_grants_chunk_id_document_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_chunk_grants_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "chunk_id",
            "effect",
            "subject_type",
            "subject_value",
            name=op.f("pk_document_chunk_grants"),
        ),
    )
    op.create_index(
        op.f("ix_document_chunk_grants_organization_id"),
        "document_chunk_grants",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunk_grants_subject",
        "document_chunk_grants",
        ["organization_id", "subject_type", "subject_value", "effect"],
        unique=False,
    )
    for acl_key, effect, subject_type in (
        ("users", "ALLOW", "USER"),
        ("roles", "ALLOW", "ROLE"),
        ("departments", "ALLOW", "DEPARTMENT"),
        ("deny_users", "DENY", "USER"),
        ("deny_roles", "DENY", "ROLE"),
        ("deny_departments", "DENY", "DEPARTMENT"),
    ):
        op.execute(
            sa.text(
                "INSERT INTO document_chunk_grants "
                "(chunk_id, effect, subject_type, subject_value, created_at, organization_id) "
                "SELECT chunk.id, :effect, :subject_type, value, now(), chunk.organization_id "
                "FROM document_chunks AS chunk "
                "CROSS JOIN LATERAL jsonb_array_elements_text("
                "COALESCE(chunk.acl::jsonb -> :acl_key, '[]'::jsonb)) AS value"
            ).bindparams(effect=effect, subject_type=subject_type, acl_key=acl_key)
        )
    op.execute(
        "INSERT INTO document_chunk_grants "
        "(chunk_id, effect, subject_type, subject_value, created_at, organization_id) "
        "SELECT id, 'ALLOW', 'ORGANIZATION', organization_id::text, now(), organization_id "
        "FROM document_chunks "
        "WHERE COALESCE((acl::jsonb ->> 'organization')::boolean, false)"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_content_fts ON document_chunks "
        "USING gin (to_tsvector('simple'::regconfig, content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_fts")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.drop_index("ix_document_chunk_grants_subject", table_name="document_chunk_grants")
    op.drop_index(
        op.f("ix_document_chunk_grants_organization_id"),
        table_name="document_chunk_grants",
    )
    op.drop_table("document_chunk_grants")
    op.drop_column("document_chunks", "embedding")
    op.drop_column("model_calls", "operation")
    op.execute("DELETE FROM model_calls WHERE run_id IS NULL")
    op.alter_column("model_calls", "run_id", existing_type=sa.Uuid(), nullable=False)
