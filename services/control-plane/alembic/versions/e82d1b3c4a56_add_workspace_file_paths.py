"""add workspace file paths

Revision ID: e82d1b3c4a56
Revises: d72b0a5e2f34
Create Date: 2026-08-29 23:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e82d1b3c4a56"
down_revision: str | None = "d72b0a5e2f34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("path", sa.String(length=512), nullable=True))
    op.add_column("artifacts", sa.Column("file_version", sa.Integer(), nullable=True))
    op.add_column(
        "artifacts",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_artifacts_workspace_path_version",
        "artifacts",
        ["organization_id", "workspace_id", "path", "file_version"],
    )
    op.create_index(
        "uq_artifacts_workspace_current_path",
        "artifacts",
        ["organization_id", "workspace_id", "path"],
        unique=True,
        sqlite_where=sa.text("path IS NOT NULL AND superseded_at IS NULL"),
        postgresql_where=sa.text("path IS NOT NULL AND superseded_at IS NULL"),
    )
    op.create_check_constraint(
        "positive_artifact_file_version",
        "artifacts",
        "path IS NULL OR (file_version IS NOT NULL AND file_version > 0)",
    )


def downgrade() -> None:
    op.drop_constraint("positive_artifact_file_version", "artifacts", type_="check")
    op.drop_index("uq_artifacts_workspace_current_path", table_name="artifacts")
    op.drop_constraint("uq_artifacts_workspace_path_version", "artifacts", type_="unique")
    op.drop_column("artifacts", "superseded_at")
    op.drop_column("artifacts", "file_version")
    op.drop_column("artifacts", "path")
