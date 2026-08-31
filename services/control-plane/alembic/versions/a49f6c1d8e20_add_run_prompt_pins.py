"""add run prompt pins

Revision ID: a49f6c1d8e20
Revises: f34b8d1e2c90
Create Date: 2026-08-29 21:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a49f6c1d8e20"
down_revision: str | None = "f34b8d1e2c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("prompt_pins", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column("runs", "prompt_pins", server_default=None)


def downgrade() -> None:
    op.drop_column("runs", "prompt_pins")
