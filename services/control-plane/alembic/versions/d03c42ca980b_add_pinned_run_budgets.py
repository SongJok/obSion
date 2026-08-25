"""add pinned run budgets

Revision ID: d03c42ca980b
Revises: b5237a3c5f80
Create Date: 2026-08-24 21:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d03c42ca980b"
down_revision: str | None = "b5237a3c5f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("max_input_tokens", sa.Integer(), server_default="120000", nullable=False),
    )
    op.add_column(
        "runs",
        sa.Column("max_output_tokens", sa.Integer(), server_default="16000", nullable=False),
    )
    op.add_column(
        "runs",
        sa.Column(
            "max_cost_amount",
            sa.Numeric(precision=18, scale=8),
            server_default="10",
            nullable=False,
        ),
    )
    op.alter_column("runs", "max_input_tokens", server_default=None)
    op.alter_column("runs", "max_output_tokens", server_default=None)
    op.alter_column("runs", "max_cost_amount", server_default=None)
    op.create_check_constraint(
        op.f("ck_runs_positive_max_input_tokens"),
        "runs",
        "max_input_tokens > 0",
    )
    op.create_check_constraint(
        op.f("ck_runs_positive_max_output_tokens"),
        "runs",
        "max_output_tokens > 0",
    )
    op.create_check_constraint(
        op.f("ck_runs_positive_max_cost_amount"),
        "runs",
        "max_cost_amount > 0",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_runs_positive_max_cost_amount"), "runs", type_="check")
    op.drop_constraint(op.f("ck_runs_positive_max_output_tokens"), "runs", type_="check")
    op.drop_constraint(op.f("ck_runs_positive_max_input_tokens"), "runs", type_="check")
    op.drop_column("runs", "max_cost_amount")
    op.drop_column("runs", "max_output_tokens")
    op.drop_column("runs", "max_input_tokens")
