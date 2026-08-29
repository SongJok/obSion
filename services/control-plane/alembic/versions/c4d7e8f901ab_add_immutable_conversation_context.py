"""add immutable conversation context

Revision ID: c4d7e8f901ab
Revises: b7d1a5e492c3
Create Date: 2026-08-25 23:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d7e8f901ab"
down_revision: str | None = "b7d1a5e492c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_conversation_snapshots",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_thread_id", sa.Uuid(), nullable=False),
        sa.Column("source_turn_id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("source_principal_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("user_content", sa.Text(), nullable=False),
        sa.Column("assistant_content", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "classification",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "length(content_fingerprint) = 64",
            name=op.f("ck_run_conversation_snapshots_conversation_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name=op.f("ck_run_conversation_snapshots_positive_conversation_ordinal"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_run_conversation_snapshots_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_run_conversation_snapshots_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["artifacts.id"],
            name=op.f("fk_run_conversation_snapshots_source_artifact_id_artifacts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_principal_id"],
            ["users.id"],
            name=op.f("fk_run_conversation_snapshots_source_principal_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["runs.id"],
            name=op.f("fk_run_conversation_snapshots_source_run_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_thread_id"],
            ["threads.id"],
            name=op.f("fk_run_conversation_snapshots_source_thread_id_threads"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id"],
            ["turns.id"],
            name=op.f("fk_run_conversation_snapshots_source_turn_id_turns"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_conversation_snapshots")),
        sa.UniqueConstraint(
            "run_id",
            "ordinal",
            name="uq_run_conversation_snapshots_ordinal",
        ),
        sa.UniqueConstraint(
            "run_id",
            "source_turn_id",
            name="uq_run_conversation_snapshots_turn",
        ),
    )
    for column in (
        "organization_id",
        "run_id",
        "source_artifact_id",
        "source_run_id",
        "source_thread_id",
        "source_turn_id",
    ):
        op.create_index(
            op.f(f"ix_run_conversation_snapshots_{column}"),
            "run_conversation_snapshots",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_run_conversation_snapshots_run",
        "run_conversation_snapshots",
        ["organization_id", "run_id", "ordinal"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_conversation_snapshots_immutable
        BEFORE UPDATE OR DELETE ON run_conversation_snapshots
        FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_run_conversation_snapshots_immutable "
        "ON run_conversation_snapshots"
    )
    op.drop_index(
        "ix_run_conversation_snapshots_run",
        table_name="run_conversation_snapshots",
    )
    for column in reversed(
        (
            "organization_id",
            "run_id",
            "source_artifact_id",
            "source_run_id",
            "source_thread_id",
            "source_turn_id",
        )
    ):
        op.drop_index(
            op.f(f"ix_run_conversation_snapshots_{column}"),
            table_name="run_conversation_snapshots",
        )
    op.drop_table("run_conversation_snapshots")
