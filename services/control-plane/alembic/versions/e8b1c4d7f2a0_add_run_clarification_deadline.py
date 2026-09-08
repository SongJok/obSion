"""add run clarification deadline

Revision ID: e8b1c4d7f2a0
Revises: d7f31a9c4b28
Create Date: 2026-09-04 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8b1c4d7f2a0"
down_revision: str | None = "d7f31a9c4b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("waiting_user_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_runs_waiting_user_expires_at"),
        "runs",
        ["waiting_user_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_waiting_user_expires_at"), table_name="runs")
    op.drop_column("runs", "waiting_user_expires_at")
