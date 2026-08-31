"""add run context budget

Revision ID: b50f8e3d0c12
Revises: a49f6c1d8e20
Create Date: 2026-08-29 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b50f8e3d0c12"
down_revision: str | None = "a49f6c1d8e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("context_budget", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("runs", "context_budget", server_default=None)


def downgrade() -> None:
    op.drop_column("runs", "context_budget")
