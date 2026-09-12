"""Mark legacy unknown deliveries as requiring reconciliation, without resending.

Revision ID: b4c6d8e0f2a4
Revises: a3b5c7d9e1f2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4c6d8e0f2a4"
down_revision: str | None = "a3b5c7d9e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The original UNKNOWN backfill predates reconciliation_required_at. Record
    # when reconciliation became required; this is never a vendor delivery time.
    # Existing guards remain active and reject other inconsistent receipt fields.
    op.execute("""
        UPDATE im_deliveries
        SET reconciliation_required_at = CURRENT_TIMESTAMP
        WHERE status = 'UNKNOWN' AND reconciliation_required_at IS NULL
    """)


def downgrade() -> None:
    # This factual reconciliation requirement remains valid on the previous
    # schema. Removing it would make legacy rows inconsistent again.
    pass
