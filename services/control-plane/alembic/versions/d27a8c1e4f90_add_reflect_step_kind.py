"""add REFLECT to persisted Harness step kinds

Revision ID: d27a8c1e4f90
Revises: c23e1d4a9b70
Create Date: 2026-08-29 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d27a8c1e4f90"
down_revision: str | None = "c23e1d4a9b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STEP_KINDS = (
    "OBSERVE",
    "UNDERSTAND",
    "PLAN",
    "MODEL",
    "CAPABILITY",
    "VERIFY",
    "REFLECT",
    "RESPOND",
)


def _widen_varchar_enum(table: str, column: str, values: Sequence[str]) -> None:
    quoted = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = :table
              AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) ILIKE :needle
            """
        ),
        {"table": table, "needle": f"%{column}%IN%"},
    ).fetchall()
    for (name,) in rows:
        op.drop_constraint(name, table_name=table, type_="check")
    op.create_check_constraint(
        f"ck_{table}_{column}_values",
        table_name=table,
        condition=f"{column} IN ({quoted})",
    )


def upgrade() -> None:
    _widen_varchar_enum("run_steps", "kind", _STEP_KINDS)


def downgrade() -> None:
    op.execute(sa.text("UPDATE run_steps SET kind = 'VERIFY' WHERE kind = 'REFLECT'"))
    _widen_varchar_enum(
        "run_steps",
        "kind",
        (
            "OBSERVE",
            "UNDERSTAND",
            "PLAN",
            "MODEL",
            "CAPABILITY",
            "VERIFY",
            "RESPOND",
        ),
    )
