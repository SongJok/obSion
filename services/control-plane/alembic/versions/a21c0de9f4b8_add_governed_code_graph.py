"""add governed code graph

Revision ID: a21c0de9f4b8
Revises: 19c6b2e4a7d1
Create Date: 2026-08-29 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a21c0de9f4b8"
down_revision: str | None = "19c6b2e4a7d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def upgrade() -> None:
    op.create_table(
        "code_repositories",
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("default_branch", sa.String(length=200), nullable=False),
        sa.Column(
            "classification",
            _enum("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", name="classification"),
            nullable=False,
        ),
        sa.Column("acl", sa.JSON(), nullable=False),
        sa.Column("current_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name"),
    )
    op.create_index(
        op.f("ix_code_repositories_organization_id"),
        "code_repositories",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "code_snapshots",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("commit_id", sa.String(length=200), nullable=False),
        sa.Column("parser_version", sa.String(length=120), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("symbol_count", sa.Integer(), nullable=False),
        sa.Column("content_checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("ordinal > 0", name="positive_code_snapshot_ordinal"),
        sa.CheckConstraint("file_count >= 0", name="nonnegative_code_snapshot_file_count"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["code_repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_id", "ordinal"),
    )
    op.create_index(
        op.f("ix_code_snapshots_organization_id"),
        "code_snapshots",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_snapshots_repository_id"), "code_snapshots", ["repository_id"], unique=False
    )
    op.create_table(
        "code_source_files",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("language", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("parse_error", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["code_repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["code_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "path"),
    )
    op.create_index(
        op.f("ix_code_source_files_organization_id"),
        "code_source_files",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_source_files_snapshot_id"), "code_source_files", ["snapshot_id"], unique=False
    )
    op.create_index(
        op.f("ix_code_source_files_repository_id"),
        "code_source_files",
        ["repository_id"],
        unique=False,
    )
    op.create_table(
        "code_symbols",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            _enum("MODULE", "CLASS", "FUNCTION", "METHOD", "API", "TABLE", name="codesymbolkind"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=400), nullable=False),
        sa.Column("qualified_name", sa.String(length=1000), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=1000), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["code_source_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["code_repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["code_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_code_symbols_qualified_name",
        "code_symbols",
        ["organization_id", "qualified_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_symbols_organization_id"), "code_symbols", ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_code_symbols_snapshot_id"), "code_symbols", ["snapshot_id"], unique=False
    )
    op.create_index(
        op.f("ix_code_symbols_repository_id"), "code_symbols", ["repository_id"], unique=False
    )
    op.create_index(op.f("ix_code_symbols_file_id"), "code_symbols", ["file_id"], unique=False)
    op.create_table(
        "code_graph_edges",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("from_symbol_id", sa.Uuid(), nullable=False),
        sa.Column("to_symbol_id", sa.Uuid(), nullable=True),
        sa.Column(
            "relation",
            _enum(
                "CONTAINS",
                "CALLS",
                "REFERENCES",
                "DEPENDS_ON",
                "READS_TABLE",
                "WRITES_TABLE",
                "EXPOSES_API",
                name="coderelation",
            ),
            nullable=False,
        ),
        sa.Column("to_name", sa.String(length=1000), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["from_symbol_id"], ["code_symbols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["code_repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["code_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_symbol_id"], ["code_symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_code_graph_edges_from",
        "code_graph_edges",
        ["organization_id", "from_symbol_id", "relation"],
        unique=False,
    )
    op.create_index(
        "ix_code_graph_edges_to",
        "code_graph_edges",
        ["organization_id", "to_symbol_id", "relation"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_graph_edges_organization_id"),
        "code_graph_edges",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_graph_edges_snapshot_id"), "code_graph_edges", ["snapshot_id"], unique=False
    )
    op.create_index(
        op.f("ix_code_graph_edges_repository_id"),
        "code_graph_edges",
        ["repository_id"],
        unique=False,
    )
    op.create_table(
        "code_repository_grants",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("effect", sa.String(length=12), nullable=False),
        sa.Column("subject_type", sa.String(length=24), nullable=False),
        sa.Column("subject_value", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("effect IN ('ALLOW', 'DENY')", name="valid_code_grant_effect"),
        sa.CheckConstraint(
            "subject_type IN ('USER', 'ROLE', 'DEPARTMENT', 'ORGANIZATION')",
            name="valid_code_grant_subject_type",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["code_repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("repository_id", "effect", "subject_type", "subject_value"),
    )
    op.create_index(
        "ix_code_repository_grants_subject",
        "code_repository_grants",
        ["organization_id", "subject_type", "subject_value", "effect"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_repository_grants_organization_id"),
        "code_repository_grants",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("code_repository_grants")
    op.drop_table("code_graph_edges")
    op.drop_table("code_symbols")
    op.drop_table("code_source_files")
    op.drop_table("code_snapshots")
    op.drop_table("code_repositories")
