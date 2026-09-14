"""Persist actual source scan starts; never backfill fictitious freshness.

Revision ID: c5d7e9f1a3b5
Revises: b4c6d8e0f2a4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d7e9f1a3b5"
down_revision: str | None = "b4c6d8e0f2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_sync_sources", sa.Column("scan_started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "knowledge_sync_sources", sa.Column("completed_scan_started_at", sa.DateTime(timezone=True))
    )
    op.add_column("knowledge_sync_sources", sa.Column("completed_generation", sa.Integer()))
    op.create_check_constraint(
        op.f("ck_knowledge_sync_sources_ordered_completed_scan"),
        "knowledge_sync_sources",
        "(completed_generation IS NULL AND completed_scan_started_at IS NULL) OR "
        "(completed_generation IS NOT NULL AND completed_generation > 0 AND "
        "completed_generation <= generation AND "
        "completed_scan_started_at IS NOT NULL AND last_success_at IS NOT NULL AND "
        "completed_scan_started_at <= last_success_at)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_knowledge_sync_sources_ordered_completed_scan"),
        "knowledge_sync_sources",
        type_="check",
    )
    for column in ("completed_generation", "completed_scan_started_at", "scan_started_at"):
        op.drop_column("knowledge_sync_sources", column)
