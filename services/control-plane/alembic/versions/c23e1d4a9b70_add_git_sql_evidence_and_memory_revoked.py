"""add git/sql evidence types and revoked memory

Revision ID: c23e1d4a9b70
Revises: a21c0de9f4b8
Create Date: 2026-08-29 10:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c23e1d4a9b70"
down_revision: str | None = "a21c0de9f4b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVIDENCE_TYPES = (
    "DOCUMENT",
    "DATA",
    "SQL",
    "METRIC",
    "LOG",
    "TRACE",
    "CODE",
    "GIT",
    "DEPLOYMENT",
    "CONFIG",
    "TOOL",
)
_MEMORY_STATUSES = (
    "CANDIDATE",
    "APPROVED",
    "REJECTED",
    "EXPIRED",
    "REVOKED",
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
    _widen_varchar_enum("evidence", "evidence_type", _EVIDENCE_TYPES)
    _widen_varchar_enum("memories", "status", _MEMORY_STATUSES)


def downgrade() -> None:
    op.execute(sa.text("UPDATE evidence SET evidence_type = 'CODE' WHERE evidence_type = 'GIT'"))
    op.execute(sa.text("UPDATE evidence SET evidence_type = 'DATA' WHERE evidence_type = 'SQL'"))
    op.execute(sa.text("UPDATE memories SET status = 'REJECTED' WHERE status = 'REVOKED'"))
    _widen_varchar_enum(
        "evidence",
        "evidence_type",
        (
            "DOCUMENT",
            "DATA",
            "METRIC",
            "LOG",
            "TRACE",
            "DEPLOYMENT",
            "CONFIG",
            "CODE",
            "TOOL",
        ),
    )
    _widen_varchar_enum(
        "memories",
        "status",
        ("CANDIDATE", "APPROVED", "REJECTED", "EXPIRED"),
    )
