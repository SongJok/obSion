"""add registry definition active_version

Revision ID: f34b8d1e2c90
Revises: e31b7c2d8a01
Create Date: 2026-08-29 14:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f34b8d1e2c90"
down_revision: str | None = "e31b7c2d8a01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_definitions", sa.Column("active_version", sa.Integer(), nullable=True))
    op.add_column("skill_definitions", sa.Column("active_version", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "positive_active_agent_version",
        "agent_definitions",
        "active_version IS NULL OR active_version > 0",
    )
    op.create_check_constraint(
        "positive_active_skill_version",
        "skill_definitions",
        "active_version IS NULL OR active_version > 0",
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_definitions AS definition
            SET active_version = (
                SELECT MAX(version.version)
                FROM agent_versions AS version
                WHERE version.agent_id = definition.id
            )
            WHERE definition.status = 'ACTIVE'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE skill_definitions AS definition
            SET active_version = (
                SELECT MAX(version.version)
                FROM skill_versions AS version
                WHERE version.skill_id = definition.id
            )
            WHERE definition.status = 'ACTIVE'
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint("positive_active_skill_version", "skill_definitions", type_="check")
    op.drop_constraint("positive_active_agent_version", "agent_definitions", type_="check")
    op.drop_column("skill_definitions", "active_version")
    op.drop_column("agent_definitions", "active_version")
