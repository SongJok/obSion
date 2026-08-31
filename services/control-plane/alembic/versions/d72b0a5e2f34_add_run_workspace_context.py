"""add run workspace context

Revision ID: d72b0a5e2f34
Revises: c61a9f4e1d23
Create Date: 2026-08-29 23:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d72b0a5e2f34"
down_revision: str | None = "c61a9f4e1d23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "workspace_context",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("runs", "workspace_context", server_default=None)


def downgrade() -> None:
    op.drop_column("runs", "workspace_context")
