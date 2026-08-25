"""add evaluation evidence and regression gates

Revision ID: 8ab0d4f7c2e1
Revises: 59b7457d48dc
Create Date: 2026-08-25 15:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8ab0d4f7c2e1"
down_revision: str | None = "59b7457d48dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evaluation_cases",
        sa.Column(
            "evaluator",
            sa.Enum(
                "ROUTING",
                "SQL_POLICY",
                "RUN_OUTPUT",
                name="evaluationtarget",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE evaluation_cases
        SET evaluator = CASE
          WHEN expected::jsonb ? 'sql_allowed' THEN 'SQL_POLICY'
          WHEN input_payload::jsonb ? 'run_id' THEN 'RUN_OUTPUT'
          ELSE 'ROUTING'
        END
        """
    )
    op.alter_column("evaluation_cases", "evaluator", nullable=False)

    op.add_column("evaluation_runs", sa.Column("requested_by", sa.Uuid(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("baseline_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "dataset_snapshot_sha256",
            sa.String(length=64),
            server_default=sa.text("repeat('0', 64)"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "snapshot_sha256",
            sa.String(length=64),
            server_default=sa.text("repeat('0', 64)"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "configuration_snapshot",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )
    op.add_column("evaluation_runs", sa.Column("gate_passed", sa.Boolean(), nullable=True))
    op.add_column(
        "evaluation_runs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "evaluation_runs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_evaluation_runs_requested_by_users",
        "evaluation_runs",
        "users",
        ["requested_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_evaluation_runs_baseline_run_id_evaluation_runs",
        "evaluation_runs",
        "evaluation_runs",
        ["baseline_run_id"],
        ["id"],
    )
    op.alter_column("evaluation_runs", "dataset_snapshot_sha256", server_default=None)
    op.alter_column("evaluation_runs", "snapshot_sha256", server_default=None)
    op.alter_column("evaluation_runs", "configuration_snapshot", server_default=None)

    op.create_table(
        "evaluation_case_results",
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_case_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column(
            "evaluator",
            sa.Enum(
                "ROUTING",
                "SQL_POLICY",
                "RUN_OUTPUT",
                name="evaluationtarget",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PASSED",
                "FAILED",
                "ERROR",
                name="evaluationresultstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("case_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("observed", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name="non_negative_duration",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_case_id"],
            ["evaluation_cases.id"],
            name="fk_evaluation_case_results_evaluation_case_id_evaluation_cases",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name="fk_evaluation_case_results_evaluation_run_id_evaluation_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_evaluation_case_results_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_case_results"),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "evaluation_case_id",
            name="uq_evaluation_case_results_evaluation_run_id",
        ),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "ordinal",
            name="uq_evaluation_case_results_evaluation_run_id_ordinal",
        ),
    )
    op.create_index(
        op.f("ix_evaluation_case_results_evaluation_run_id"),
        "evaluation_case_results",
        ["evaluation_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evaluation_case_results_organization_id"),
        "evaluation_case_results",
        ["organization_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER trg_evaluation_case_results_immutable
        BEFORE UPDATE OR DELETE ON evaluation_case_results
        FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION obsion_guard_completed_evaluation_run()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.status IN ('COMPLETED', 'FAILED') THEN
            RAISE EXCEPTION 'completed evaluation run cannot be changed or deleted'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evaluation_runs_terminal_immutable
        BEFORE UPDATE OR DELETE ON evaluation_runs
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_completed_evaluation_run()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_evaluation_runs_terminal_immutable ON evaluation_runs")
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_completed_evaluation_run()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_evaluation_case_results_immutable ON evaluation_case_results"
    )
    op.drop_index(
        op.f("ix_evaluation_case_results_organization_id"),
        table_name="evaluation_case_results",
    )
    op.drop_index(
        op.f("ix_evaluation_case_results_evaluation_run_id"),
        table_name="evaluation_case_results",
    )
    op.drop_table("evaluation_case_results")
    op.drop_constraint(
        "fk_evaluation_runs_baseline_run_id_evaluation_runs",
        "evaluation_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_evaluation_runs_requested_by_users",
        "evaluation_runs",
        type_="foreignkey",
    )
    for column in (
        "completed_at",
        "started_at",
        "gate_passed",
        "configuration_snapshot",
        "snapshot_sha256",
        "dataset_snapshot_sha256",
        "baseline_run_id",
        "requested_by",
    ):
        op.drop_column("evaluation_runs", column)
    op.drop_column("evaluation_cases", "evaluator")
