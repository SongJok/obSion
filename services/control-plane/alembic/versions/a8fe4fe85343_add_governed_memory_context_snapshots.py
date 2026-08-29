"""add governed memory context snapshots

Revision ID: a8fe4fe85343
Revises: 8ab0d4f7c2e1
Create Date: 2026-08-25 16:54:02.042349
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8fe4fe85343"
down_revision: str | None = "8ab0d4f7c2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_memory_snapshots",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "TURN",
                "SESSION",
                "WORKSPACE",
                "USER_PREFERENCE",
                name="memoryscope",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("owner_ref", sa.String(length=300), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "sensitivity",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=False),
        sa.Column("memory_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "ordinal > 0",
            name=op.f("ck_run_memory_snapshots_positive_memory_snapshot_ordinal"),
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
            name=op.f("fk_run_memory_snapshots_memory_id_memories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_run_memory_snapshots_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id"],
            ["policy_decisions.id"],
            name=op.f("fk_run_memory_snapshots_policy_decision_id_policy_decisions"),
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["users.id"],
            name=op.f("fk_run_memory_snapshots_principal_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_run_memory_snapshots_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_memory_snapshots")),
        sa.UniqueConstraint("run_id", "memory_id", name="uq_run_memory_snapshots_memory"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_run_memory_snapshots_ordinal"),
    )
    op.create_index(
        op.f("ix_run_memory_snapshots_memory_id"),
        "run_memory_snapshots",
        ["memory_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_memory_snapshots_organization_id"),
        "run_memory_snapshots",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_run_memory_snapshots_run",
        "run_memory_snapshots",
        ["organization_id", "run_id", "ordinal"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_memory_snapshots_run_id"),
        "run_memory_snapshots",
        ["run_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER trg_run_memory_snapshots_immutable
        BEFORE UPDATE OR DELETE ON run_memory_snapshots
        FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION obsion_guard_memory_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'governed memory cannot be deleted directly'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.organization_id IS DISTINCT FROM NEW.organization_id
             OR OLD.scope IS DISTINCT FROM NEW.scope
             OR OLD.owner_ref IS DISTINCT FROM NEW.owner_ref
             OR OLD.content::jsonb IS DISTINCT FROM NEW.content::jsonb
             OR OLD.dedupe_key IS DISTINCT FROM NEW.dedupe_key
             OR OLD.sensitivity IS DISTINCT FROM NEW.sensitivity
             OR OLD.policy_decision_id IS DISTINCT FROM NEW.policy_decision_id
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'governed memory content and lineage are immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status
             AND NOT (
               (OLD.status = 'CANDIDATE' AND NEW.status IN ('APPROVED', 'REJECTED', 'EXPIRED'))
               OR (OLD.status = 'APPROVED' AND NEW.status = 'EXPIRED')
             ) THEN
            RAISE EXCEPTION 'invalid governed memory status transition: % -> %',
              OLD.status, NEW.status
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_memories_governed_mutation
        BEFORE UPDATE OR DELETE ON memories
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_memory_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_memories_governed_mutation ON memories")
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_memory_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_run_memory_snapshots_immutable ON run_memory_snapshots")
    op.drop_index(op.f("ix_run_memory_snapshots_run_id"), table_name="run_memory_snapshots")
    op.drop_index("ix_run_memory_snapshots_run", table_name="run_memory_snapshots")
    op.drop_index(
        op.f("ix_run_memory_snapshots_organization_id"),
        table_name="run_memory_snapshots",
    )
    op.drop_index(
        op.f("ix_run_memory_snapshots_memory_id"),
        table_name="run_memory_snapshots",
    )
    op.drop_table("run_memory_snapshots")
