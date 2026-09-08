"""Persist read-only DingTalk Outbox reconciliation evidence."""

import sqlalchemy as sa
from alembic import op

revision = "a86e47f58a69"
down_revision = "a85e36f47a58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("vendor_send_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("vendor_read_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("vendor_read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column(
            "reconciliation_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "dingtalk_robot_outbox",
        "reconciliation_attempt_count",
        server_default=None,
    )
    op.create_check_constraint(
        op.f("ck_dingtalk_robot_outbox_reconciliation_attempts_nonnegative"),
        "dingtalk_robot_outbox",
        "reconciliation_attempt_count >= 0",
    )
    op.create_index(
        op.f("ix_dingtalk_robot_outbox_last_reconciled_at"),
        "dingtalk_robot_outbox",
        ["last_reconciled_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE dingtalk_robot_outbox IN ACCESS EXCLUSIVE MODE"))
    unsafe = connection.scalar(
        sa.text(
            "SELECT count(*) FROM dingtalk_robot_outbox "
            "WHERE last_reconciled_at IS NOT NULL OR reconciliation_attempt_count <> 0"
        )
    )
    if unsafe:
        raise RuntimeError("Reconcile all DingTalk Outbox evidence before downgrading")
    op.drop_index(
        op.f("ix_dingtalk_robot_outbox_last_reconciled_at"),
        table_name="dingtalk_robot_outbox",
    )
    op.drop_constraint(
        op.f("ck_dingtalk_robot_outbox_reconciliation_attempts_nonnegative"),
        "dingtalk_robot_outbox",
        type_="check",
    )
    op.drop_column("dingtalk_robot_outbox", "reconciliation_attempt_count")
    op.drop_column("dingtalk_robot_outbox", "last_reconciled_at")
    op.drop_column("dingtalk_robot_outbox", "vendor_read_at")
    op.drop_column("dingtalk_robot_outbox", "vendor_read_status")
    op.drop_column("dingtalk_robot_outbox", "vendor_send_status")
