"""add operator Capability idempotency ledger

Revision ID: a79c4d2e8f10
Revises: f62c1a9e4d20
Create Date: 2026-08-31 00:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a79c4d2e8f10"
down_revision: str | None = "f62c1a9e4d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sha256_hex_check(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return f"length({column}) = 64 AND length({remainder}) = 0"


def upgrade() -> None:
    op.create_table(
        "operator_capability_invocations",
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("capability_name", sa.String(length=160), nullable=False),
        sa.Column("capability_version_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            _sha256_hex_check("input_fingerprint"),
            name=op.f("ck_operator_capability_invocations_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "status IN ('IN_PROGRESS', 'COMPLETED', 'FAILED', 'UNKNOWN')",
            name=op.f("ck_operator_capability_invocations_valid_status"),
        ),
        sa.CheckConstraint(
            "(status = 'IN_PROGRESS' AND result IS NULL AND error_code IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'COMPLETED' AND result IS NOT NULL AND error_code IS NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'FAILED' AND result IS NOT NULL AND error_code IS NOT NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'UNKNOWN' AND result IS NULL AND error_code IS NOT NULL "
            "AND completed_at IS NOT NULL)",
            name=op.f("ck_operator_capability_invocations_completion_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["capability_version_id"],
            ["capability_versions.id"],
            name=op.f(
                "fk_operator_capability_invocations_capability_version_id_capability_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name=op.f("fk_operator_capability_invocations_connector_id_connectors"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_operator_capability_invocations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "principal_id"],
            ["users.organization_id", "users.id"],
            name="fk_operator_capability_invocations_org_principal",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id"],
            ["policy_decisions.id"],
            name=op.f("fk_operator_capability_invocations_policy_decision_id_policy_decisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operator_capability_invocations")),
        sa.UniqueConstraint(
            "organization_id",
            "principal_id",
            "request_id",
            name="uq_operator_capability_invocation_request",
        ),
    )
    op.create_index(
        op.f("ix_operator_capability_invocations_organization_id"),
        "operator_capability_invocations",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operator_capability_invocations_principal_id"),
        "operator_capability_invocations",
        ["principal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operator_capability_invocations_status"),
        "operator_capability_invocations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operator_capability_invocations_expires_at"),
        "operator_capability_invocations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_operator_capability_invocations_lookup",
        "operator_capability_invocations",
        ["organization_id", "principal_id", "request_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION obsion_guard_operator_capability_invocation_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.expires_at > CURRENT_TIMESTAMP THEN
                    RAISE EXCEPTION 'operator Capability invocation retention has not expired';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
               OR OLD.principal_id IS DISTINCT FROM NEW.principal_id
               OR OLD.request_id IS DISTINCT FROM NEW.request_id
               OR OLD.capability_name IS DISTINCT FROM NEW.capability_name
               OR OLD.capability_version_id IS DISTINCT FROM NEW.capability_version_id
               OR OLD.connector_id IS DISTINCT FROM NEW.connector_id
               OR OLD.policy_decision_id IS DISTINCT FROM NEW.policy_decision_id
               OR OLD.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
               OR OLD.status <> 'IN_PROGRESS'
               OR NEW.status NOT IN ('COMPLETED', 'FAILED', 'UNKNOWN') THEN
                RAISE EXCEPTION 'operator Capability invocation identity or outcome is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_operator_capability_invocations_guard
        BEFORE UPDATE OR DELETE ON operator_capability_invocations
        FOR EACH ROW EXECUTE FUNCTION
            obsion_guard_operator_capability_invocation_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_operator_capability_invocations_guard "
        "ON operator_capability_invocations"
    )
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_operator_capability_invocation_mutation()")
    op.drop_index(
        "ix_operator_capability_invocations_lookup",
        table_name="operator_capability_invocations",
    )
    op.drop_index(
        op.f("ix_operator_capability_invocations_expires_at"),
        table_name="operator_capability_invocations",
    )
    op.drop_index(
        op.f("ix_operator_capability_invocations_status"),
        table_name="operator_capability_invocations",
    )
    op.drop_index(
        op.f("ix_operator_capability_invocations_principal_id"),
        table_name="operator_capability_invocations",
    )
    op.drop_index(
        op.f("ix_operator_capability_invocations_organization_id"),
        table_name="operator_capability_invocations",
    )
    op.drop_table("operator_capability_invocations")
