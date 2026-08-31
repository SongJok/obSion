"""add run conversation compact

Revision ID: c61a9f4e1d23
Revises: b50f8e3d0c12
Create Date: 2026-08-29 23:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c61a9f4e1d23"
down_revision: str | None = "b50f8e3d0c12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "conversation_compact",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("runs", "conversation_compact", server_default=None)


def downgrade() -> None:
    op.drop_column("runs", "conversation_compact")
