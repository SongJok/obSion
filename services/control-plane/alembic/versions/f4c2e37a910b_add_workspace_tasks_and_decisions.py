"""add governed workspace tasks and decisions

Revision ID: f4c2e37a910b
Revises: a8fe4fe85343
Create Date: 2026-08-25 18:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4c2e37a910b"
down_revision: str | None = "a8fe4fe85343"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_tasks",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "IN_PROGRESS",
                "BLOCKED",
                "COMPLETED",
                "CANCELLED",
                name="workspacetaskstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(
                "LOW",
                "NORMAL",
                "HIGH",
                "CRITICAL",
                name="workspacetaskpriority",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "version > 0", name=op.f("ck_workspace_tasks_positive_workspace_task_version")
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name=op.f("ck_workspace_tasks_nonempty_workspace_task_title"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_workspace_tasks_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_workspace_tasks_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspace_tasks_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["runs.id"],
            name=op.f("fk_workspace_tasks_source_run_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_tasks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_tasks")),
    )
    for column in (
        "assignee_id",
        "organization_id",
        "priority",
        "source_run_id",
        "status",
        "workspace_id",
    ):
        op.create_index(
            op.f(f"ix_workspace_tasks_{column}"),
            "workspace_tasks",
            [column],
            unique=False,
        )

    op.create_table(
        "workspace_decisions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PROPOSED",
                "ACCEPTED",
                "REJECTED",
                "SUPERSEDED",
                name="workspacedecisionstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_decision_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "current_version > 0",
            name=op.f("ck_workspace_decisions_positive_workspace_decision_version"),
        ),
        sa.CheckConstraint(
            "supersedes_decision_id IS NULL OR supersedes_decision_id <> id",
            name=op.f("ck_workspace_decisions_workspace_decision_cannot_supersede_self"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_workspace_decisions_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_workspace_decisions_decided_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspace_decisions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["runs.id"],
            name=op.f("fk_workspace_decisions_source_run_id_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"],
            ["workspace_decisions.id"],
            name=op.f("fk_workspace_decisions_supersedes_decision_id_workspace_decisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_decisions_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_decisions")),
    )
    for column in (
        "organization_id",
        "source_run_id",
        "status",
        "supersedes_decision_id",
        "workspace_id",
    ):
        op.create_index(
            op.f(f"ix_workspace_decisions_{column}"),
            "workspace_decisions",
            [column],
            unique=False,
        )

    op.create_table(
        "workspace_decision_versions",
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("alternatives", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_workspace_decision_versions_positive_decision_revision"),
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name=op.f("ck_workspace_decision_versions_nonempty_decision_title"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_workspace_decision_versions_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["workspace_decisions.id"],
            name=op.f("fk_workspace_decision_versions_decision_id_workspace_decisions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspace_decision_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_decision_versions")),
        sa.UniqueConstraint(
            "decision_id",
            "version",
            name=op.f("uq_workspace_decision_versions_decision_id"),
        ),
    )
    op.create_index(
        op.f("ix_workspace_decision_versions_decision_id"),
        "workspace_decision_versions",
        ["decision_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_decision_versions_organization_id"),
        "workspace_decision_versions",
        ["organization_id"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION obsion_guard_workspace_task_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'workspace tasks cannot be deleted directly'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.organization_id IS DISTINCT FROM NEW.organization_id
             OR OLD.workspace_id IS DISTINCT FROM NEW.workspace_id
             OR OLD.created_by IS DISTINCT FROM NEW.created_by
             OR OLD.source_run_id IS DISTINCT FROM NEW.source_run_id
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'workspace task identity and provenance are immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.version <> OLD.version + 1 THEN
            RAISE EXCEPTION 'workspace task version must increment by exactly one'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status
             AND NOT (
               (OLD.status = 'OPEN'
                   AND NEW.status IN ('IN_PROGRESS', 'BLOCKED', 'COMPLETED', 'CANCELLED'))
               OR (OLD.status = 'IN_PROGRESS'
                   AND NEW.status IN ('OPEN', 'BLOCKED', 'COMPLETED', 'CANCELLED'))
               OR (OLD.status = 'BLOCKED'
                   AND NEW.status IN ('OPEN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'))
               OR (OLD.status IN ('COMPLETED', 'CANCELLED') AND NEW.status = 'OPEN')
             ) THEN
            RAISE EXCEPTION 'invalid workspace task status transition: % -> %',
              OLD.status, NEW.status
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF (NEW.status = 'COMPLETED') <> (NEW.completed_at IS NOT NULL) THEN
            RAISE EXCEPTION 'workspace task completion timestamp must match completed status'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_tasks_governed_mutation
        BEFORE UPDATE OR DELETE ON workspace_tasks
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_workspace_task_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION obsion_guard_workspace_decision_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'workspace decisions cannot be deleted directly'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.organization_id IS DISTINCT FROM NEW.organization_id
             OR OLD.workspace_id IS DISTINCT FROM NEW.workspace_id
             OR OLD.created_by IS DISTINCT FROM NEW.created_by
             OR OLD.source_run_id IS DISTINCT FROM NEW.source_run_id
             OR OLD.supersedes_decision_id IS DISTINCT FROM NEW.supersedes_decision_id
             OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'workspace decision identity and provenance are immutable'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.current_version NOT IN (OLD.current_version, OLD.current_version + 1) THEN
            RAISE EXCEPTION
              'workspace decision version must remain stable or increment by exactly one'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.current_version = OLD.current_version + 1
             AND (OLD.status <> 'PROPOSED' OR NEW.status <> 'PROPOSED') THEN
            RAISE EXCEPTION 'only proposed workspace decisions can be revised'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status
             AND NOT (
               (OLD.status = 'PROPOSED' AND NEW.status IN ('ACCEPTED', 'REJECTED'))
               OR (OLD.status = 'ACCEPTED' AND NEW.status = 'SUPERSEDED')
             ) THEN
            RAISE EXCEPTION 'invalid workspace decision status transition: % -> %',
              OLD.status, NEW.status
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.status IS DISTINCT FROM NEW.status
             AND NEW.current_version <> OLD.current_version THEN
            RAISE EXCEPTION 'workspace decision revision and disposition must be separate mutations'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.status = 'PROPOSED'
             AND (NEW.decided_by IS NOT NULL OR NEW.decided_at IS NOT NULL) THEN
            RAISE EXCEPTION 'proposed workspace decisions cannot have disposition metadata'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.status IN ('ACCEPTED', 'REJECTED', 'SUPERSEDED')
             AND (NEW.decided_by IS NULL OR NEW.decided_at IS NULL) THEN
            RAISE EXCEPTION 'closed workspace decisions require disposition metadata'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF OLD.status = 'ACCEPTED' AND NEW.status = 'SUPERSEDED'
             AND (OLD.decided_by IS DISTINCT FROM NEW.decided_by
                  OR OLD.decided_at IS DISTINCT FROM NEW.decided_at) THEN
            RAISE EXCEPTION 'superseding a decision cannot rewrite its disposition metadata'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_decisions_governed_mutation
        BEFORE UPDATE OR DELETE ON workspace_decisions
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_workspace_decision_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_decision_versions_immutable
        BEFORE UPDATE OR DELETE ON workspace_decision_versions
        FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_workspace_decision_versions_immutable "
        "ON workspace_decision_versions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_workspace_decisions_governed_mutation ON workspace_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_workspace_decision_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_workspace_tasks_governed_mutation ON workspace_tasks")
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_workspace_task_mutation()")
    op.drop_index(
        op.f("ix_workspace_decision_versions_organization_id"),
        table_name="workspace_decision_versions",
    )
    op.drop_index(
        op.f("ix_workspace_decision_versions_decision_id"),
        table_name="workspace_decision_versions",
    )
    op.drop_table("workspace_decision_versions")
    for column in (
        "workspace_id",
        "supersedes_decision_id",
        "status",
        "source_run_id",
        "organization_id",
    ):
        op.drop_index(
            op.f(f"ix_workspace_decisions_{column}"),
            table_name="workspace_decisions",
        )
    op.drop_table("workspace_decisions")
    for column in (
        "workspace_id",
        "status",
        "source_run_id",
        "priority",
        "organization_id",
        "assignee_id",
    ):
        op.drop_index(op.f(f"ix_workspace_tasks_{column}"), table_name="workspace_tasks")
    op.drop_table("workspace_tasks")
