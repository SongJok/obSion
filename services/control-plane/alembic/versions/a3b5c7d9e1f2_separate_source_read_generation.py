"""Separate recurring read scheduling from the verified source-access state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b5c7d9e1f2"
down_revision: str | None = "f2a4b6c8d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are eligible for a fresh read in their next/current scan.
    # This adds scheduling metadata only; it never grants or extends access.
    op.add_column(
        "knowledge_sync_items",
        sa.Column("read_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        op.f("ck_knowledge_sync_items_nonnegative_read_generation"),
        "knowledge_sync_items",
        "read_generation >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_knowledge_sync_items_nonnegative_read_generation"),
        "knowledge_sync_items",
        type_="check",
    )
    op.drop_column("knowledge_sync_items", "read_generation")
