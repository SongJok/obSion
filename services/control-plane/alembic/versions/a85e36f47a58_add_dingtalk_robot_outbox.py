"""Add the durable, single-recipient DingTalk robot Outbox."""

import sqlalchemy as sa
from alembic import op

revision = "a85e36f47a58"
down_revision = "a84e25f36a47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dingtalk_robot_outbox",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("inbox_message_id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_sender_id", sa.String(length=255), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("capability_version_id", sa.Uuid(), nullable=True),
        sa.Column("connector_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_process_query_key", sa.String(length=500), nullable=True),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=True),
        sa.Column("blocked_reason", sa.String(length=100), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_dingtalk_robot_outbox_attempts_nonnegative"),
        ),
        sa.CheckConstraint(
            "fencing_token >= 0",
            name=op.f("ck_dingtalk_robot_outbox_fencing_nonnegative"),
        ),
        sa.CheckConstraint(
            "length(content_fingerprint) = 64", name="dingtalk_outbox_content_sha256"
        ),
        sa.CheckConstraint(
            "length(connector_fingerprint) = 64", name="dingtalk_outbox_connector_sha256"
        ),
        sa.CheckConstraint(
            "length(trim(recipient_sender_id)) > 0", name="dingtalk_outbox_recipient_nonempty"
        ),
        sa.CheckConstraint(
            "status IN ('BLOCKED', 'QUEUED', 'DISPATCHING', 'ACCEPTED', 'REJECTED', 'UNKNOWN')",
            name="valid_dingtalk_robot_outbox_status",
        ),
        sa.ForeignKeyConstraint(
            ["capability_version_id"],
            ["capability_versions.id"],
            name="fk_dingtalk_robot_outbox_capability_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "connector_id"],
            ["connectors.organization_id", "connectors.id"],
            name="fk_dingtalk_robot_outbox_connector",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["im_installation_bindings.id"],
            name="fk_dingtalk_robot_outbox_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inbox_message_id"],
            ["im_inbox_messages.id"],
            name="fk_dingtalk_robot_outbox_inbox",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_dingtalk_robot_outbox_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id"],
            ["policy_decisions.id"],
            name="fk_dingtalk_robot_outbox_policy_decision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "recipient_user_id"],
            ["users.organization_id", "users.id"],
            name="fk_dingtalk_robot_outbox_recipient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_dingtalk_robot_outbox_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_dingtalk_robot_outbox_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dingtalk_robot_outbox")),
        sa.UniqueConstraint("inbox_message_id", name="uq_dingtalk_robot_outbox_inbox"),
        sa.UniqueConstraint("run_id", name="uq_dingtalk_robot_outbox_run"),
    )
    for column in (
        "organization_id",
        "installation_id",
        "inbox_message_id",
        "run_id",
        "status",
        "next_attempt_at",
        "lease_expires_at",
    ):
        op.create_index(
            op.f(f"ix_dingtalk_robot_outbox_{column}"), "dingtalk_robot_outbox", [column]
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE dingtalk_robot_outbox IN ACCESS EXCLUSIVE MODE"))
    unsafe = connection.scalar(
        sa.text("SELECT count(*) FROM dingtalk_robot_outbox WHERE status <> 'REJECTED'")
    )
    if unsafe:
        raise RuntimeError("Reconcile all non-rejected DingTalk Outbox records before downgrading")
    op.drop_table("dingtalk_robot_outbox")
