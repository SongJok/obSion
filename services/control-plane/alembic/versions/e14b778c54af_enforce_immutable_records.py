"""enforce immutable records

Revision ID: e14b778c54af
Revises: d03c42ca980b
Create Date: 2026-08-24 21:55:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e14b778c54af"
down_revision: str | None = "d03c42ca980b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "events",
    "audit_records",
    "policy_decisions",
    "evidence",
    "claims",
    "claim_evidence",
    "model_calls",
    "document_versions",
    "evaluation_cases",
    "agent_versions",
    "skill_versions",
    "prompt_versions",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION obsion_reject_immutable_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'immutable Obsion record cannot be changed: %', TG_TABLE_NAME
            USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable "  # noqa: S608 -- fixed table allowlist
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()"
        )


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}"  # noqa: S608
        )
    op.execute("DROP FUNCTION IF EXISTS obsion_reject_immutable_mutation()")
