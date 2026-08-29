"""add versioned run feedback

Revision ID: b7d1a5e492c3
Revises: f4c2e37a910b
Create Date: 2026-08-25 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d1a5e492c3"
down_revision: str | None = "f4c2e37a910b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_feedback",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "rating",
            sa.Enum(
                "HELPFUL",
                "NEEDS_IMPROVEMENT",
                name="runfeedbackrating",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
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
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_run_feedback_positive_run_feedback_version"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_run_feedback_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_run_feedback_run_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_run_feedback_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_feedback")),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "user_id",
            name=op.f("uq_run_feedback_organization_id"),
        ),
    )
    for column in ("organization_id", "rating", "run_id", "user_id"):
        op.create_index(
            op.f(f"ix_run_feedback_{column}"),
            "run_feedback",
            [column],
            unique=False,
        )

    op.execute(
        """
        CREATE FUNCTION obsion_guard_run_feedback_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'run feedback cannot be deleted directly'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.organization_id IS DISTINCT FROM NEW.organization_id
             OR OLD.run_id IS DISTINCT FROM NEW.run_id
             OR OLD.user_id IS DISTINCT FROM NEW.user_id
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'run feedback identity is immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'run feedback version must increment by exactly one'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_feedback_governed_mutation
        BEFORE UPDATE OR DELETE ON run_feedback
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_run_feedback_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_run_feedback_governed_mutation ON run_feedback")
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_run_feedback_mutation()")
    for column in ("user_id", "run_id", "rating", "organization_id"):
        op.drop_index(op.f(f"ix_run_feedback_{column}"), table_name="run_feedback")
    op.drop_table("run_feedback")
