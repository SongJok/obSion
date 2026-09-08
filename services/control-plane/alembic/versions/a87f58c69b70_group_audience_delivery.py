"""Add operator-verified IM group audiences and governed group delivery fields."""

import sqlalchemy as sa
from alembic import op

revision = "a87f58c69b70"
down_revision = "a86e47f58a69"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "im_group_audiences",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.String(length=512), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_user_ids", sa.JSON(), nullable=False),
        sa.Column("member_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("max_classification", sa.String(length=16), nullable=False),
        sa.Column("allow_final_answer", sa.Boolean(), nullable=False),
        sa.Column("allow_status", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("verification_source", sa.String(length=255), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')", name="valid_im_group_audience_status"
        ),
        sa.CheckConstraint(
            "max_classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="valid_im_group_audience_classification",
        ),
        sa.CheckConstraint(
            "length(trim(conversation_id)) > 0 AND length(member_fingerprint) = 64",
            name="im_group_audience_identity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_group_audience_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_im_group_audience_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_group_audience_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_group_audiences")),
        sa.UniqueConstraint(
            "installation_id", "conversation_id", name="uq_im_group_audience_conversation"
        ),
    )
    for column in ("organization_id", "installation_id", "workspace_id"):
        op.create_index(f"ix_im_group_audiences_{column}", "im_group_audiences", [column])

    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column(
            "conversation_type", sa.String(length=16), nullable=False, server_default="direct"
        ),
    )
    op.alter_column("dingtalk_robot_outbox", "conversation_type", server_default=None)
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("recipient_conversation_id", sa.String(length=512), nullable=True),
    )
    op.add_column("dingtalk_robot_outbox", sa.Column("audience_id", sa.Uuid(), nullable=True))
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("audience_member_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("delivery_mode", sa.String(length=16), nullable=False, server_default="FINAL"),
    )
    op.add_column(
        "dingtalk_robot_outbox",
        sa.Column("answer_classification", sa.String(length=16), nullable=True),
    )
    op.alter_column("dingtalk_robot_outbox", "delivery_mode", server_default=None)
    op.create_foreign_key(
        "fk_dingtalk_robot_outbox_audience",
        "dingtalk_robot_outbox",
        "im_group_audiences",
        ["audience_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "dingtalk_outbox_conversation_target",
        "dingtalk_robot_outbox",
        "(conversation_type = 'direct' AND audience_id IS NULL AND "
        "recipient_conversation_id IS NULL AND delivery_mode = 'FINAL') OR "
        "(conversation_type = 'group' AND audience_id IS NOT NULL AND "
        "recipient_conversation_id IS NOT NULL AND audience_member_fingerprint IS NOT NULL AND "
        "delivery_mode IN ('FINAL', 'STATUS'))",
    )
    op.create_check_constraint(
        "dingtalk_outbox_conversation_type",
        "dingtalk_robot_outbox",
        "conversation_type IN ('direct', 'group')",
    )
    op.create_check_constraint(
        "dingtalk_outbox_answer_classification",
        "dingtalk_robot_outbox",
        "answer_classification IS NULL OR answer_classification IN "
        "('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
    )
    op.create_index(
        "ix_dingtalk_robot_outbox_audience_id", "dingtalk_robot_outbox", ["audience_id"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE dingtalk_robot_outbox, im_group_audiences IN ACCESS EXCLUSIVE MODE")
        )
    unsafe = connection.scalar(
        sa.text(
            "SELECT count(*) FROM dingtalk_robot_outbox "
            "WHERE conversation_type <> 'direct' OR audience_id IS NOT NULL"
        )
    )
    if unsafe:
        raise RuntimeError("Revoke all group audience deliveries before downgrading")
    audience_rows = connection.scalar(sa.text("SELECT count(*) FROM im_group_audiences"))
    if audience_rows:
        raise RuntimeError("Revoke all IM group audiences before downgrading")
    op.drop_index("ix_dingtalk_robot_outbox_audience_id", table_name="dingtalk_robot_outbox")
    op.drop_constraint(
        op.f("ck_dingtalk_robot_outbox_dingtalk_outbox_answer_classification"),
        "dingtalk_robot_outbox",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dingtalk_robot_outbox_dingtalk_outbox_conversation_type"),
        "dingtalk_robot_outbox",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_dingtalk_robot_outbox_dingtalk_outbox_conversation_target"),
        "dingtalk_robot_outbox",
        type_="check",
    )
    op.drop_constraint(
        "fk_dingtalk_robot_outbox_audience", "dingtalk_robot_outbox", type_="foreignkey"
    )
    op.drop_column("dingtalk_robot_outbox", "answer_classification")
    op.drop_column("dingtalk_robot_outbox", "delivery_mode")
    op.drop_column("dingtalk_robot_outbox", "audience_member_fingerprint")
    op.drop_column("dingtalk_robot_outbox", "audience_id")
    op.drop_column("dingtalk_robot_outbox", "recipient_conversation_id")
    op.drop_column("dingtalk_robot_outbox", "conversation_type")
    for column in ("workspace_id", "installation_id", "organization_id"):
        op.drop_index(f"ix_im_group_audiences_{column}", table_name="im_group_audiences")
    op.drop_table("im_group_audiences")
