"""Persist the verified project revision used by a Harness Run."""

import sqlalchemy as sa
from alembic import op

revision = "a88f69d7c8a0"
down_revision = "a87f58c69b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_source_pins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("connector_version_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("commit_id", sa.String(length=64), nullable=False),
        sa.Column("tree_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_bytes", sa.Integer(), nullable=False),
        sa.Column("pinned_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_run_source_pins_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["project_sources.organization_id", "project_sources.id"],
            name="fk_run_source_pins_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "connector_version_id"],
            [
                "connector_configuration_versions.organization_id",
                "connector_configuration_versions.id",
            ],
            name="fk_run_source_pins_connector_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_run_source_pins_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "repository_id"],
            ["code_repositories.organization_id", "code_repositories.id"],
            name="fk_run_source_pins_repository",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "pinned_by"],
            ["users.organization_id", "users.id"],
            name="fk_run_source_pins_pinner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "run_id", "source_id", name="uq_run_source_pins_run_source"
        ),
        sa.CheckConstraint(
            "length(commit_id) IN (40, 64) AND length(tree_id) = length(commit_id)",
            name="run_source_pin_hash_lengths",
        ),
        sa.CheckConstraint(
            "length(snapshot_fingerprint) = 64", name="run_source_pin_fingerprint_length"
        ),
        sa.CheckConstraint("file_count >= 0", name="nonnegative_run_source_pin_file_count"),
        sa.CheckConstraint("snapshot_bytes >= 0", name="nonnegative_run_source_pin_bytes"),
    )
    for column in (
        "organization_id",
        "run_id",
        "source_id",
        "connector_version_id",
        "workspace_id",
        "repository_id",
    ):
        op.create_index(f"ix_run_source_pins_{column}", "run_source_pins", [column])
    op.execute(
        "CREATE TRIGGER trg_run_source_pins_immutable "
        "BEFORE UPDATE OR DELETE ON run_source_pins "
        "FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_run_source_pins_no_truncate "
        "BEFORE TRUNCATE ON run_source_pins "
        "FOR EACH STATEMENT EXECUTE FUNCTION obsion_reject_immutable_mutation()"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE run_source_pins IN ACCESS EXCLUSIVE MODE"))
    table = sa.table("run_source_pins", sa.column("id"))
    if connection.scalar(sa.select(table.c.id).limit(1)) is not None:
        raise RuntimeError("Refusing to drop a populated Run source pin ledger")
    op.drop_table("run_source_pins")
