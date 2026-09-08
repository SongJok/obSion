"""Add trusted IM installations and durable Inbox; legacy bindings stay unmodified."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a81b92c03d14"
down_revision: str | None = "f3d4e5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "im_installations",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_corp_id", sa.String(length=255), nullable=False),
        sa.Column("external_app_id", sa.String(length=255), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_principal_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("verification_source", sa.String(length=255), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            "provider IN ('dingtalk', 'feishu', 'wecom')",
            name=op.f("ck_im_installations_valid_installation_provider"),
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')",
            name=op.f("ck_im_installations_valid_installation_status"),
        ),
        sa.CheckConstraint(
            "length(trim(external_corp_id)) > 0 AND length(trim(external_app_id)) > 0 AND "
            "length(trim(verification_source)) > 0",
            name=op.f("ck_im_installations_installation_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name=op.f("fk_im_installations_connector_id_connectors"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "adapter_principal_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_installations_adapter",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_installations_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_installations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_installations")),
        sa.UniqueConstraint("organization_id", "id", name="uq_im_installations_org_id"),
        sa.UniqueConstraint(
            "provider", "external_corp_id", "external_app_id", name="uq_im_installations_vendor_app"
        ),
    )
    op.create_index(
        op.f("ix_im_installations_organization_id"),
        "im_installations",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "im_installation_bindings",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(trim(sender_id)) > 0",
            name=op.f("ck_im_installation_bindings_installation_sender_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_installation_bindings_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_installation_bindings_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_installation_bindings_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_installation_bindings_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_installation_bindings")),
        sa.UniqueConstraint("installation_id", "sender_id", name="uq_im_installation_sender"),
        sa.UniqueConstraint(
            "organization_id",
            "installation_id",
            "id",
            name="uq_im_installation_bindings_org_installation_id",
        ),
    )
    op.create_index(
        op.f("ix_im_installation_bindings_organization_id"),
        "im_installation_bindings",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "im_inbox_messages",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("vendor_event_id", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=512), nullable=False),
        sa.Column("conversation_type", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'PROCESSED' AND turn_id IS NOT NULL AND run_id IS NOT NULL "
            "AND processed_at IS NOT NULL) OR (status IN ('RECEIVED', 'PROCESSING') "
            "AND turn_id IS NULL AND run_id IS NULL AND processed_at IS NULL)",
            name=op.f("ck_im_inbox_messages_inbox_task_state"),
        ),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'PROCESSING', 'PROCESSED')",
            name=op.f("ck_im_inbox_messages_valid_inbox_status"),
        ),
        sa.CheckConstraint(
            "length(fingerprint) = 64", name=op.f("ck_im_inbox_messages_inbox_fingerprint_length")
        ),
        sa.CheckConstraint(
            "length(trim(vendor_event_id)) > 0 AND length(trim(sender_id)) > 0 AND "
            "length(trim(conversation_id)) > 0",
            name=op.f("ck_im_inbox_messages_inbox_identity_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id", "binding_id"],
            [
                "im_installation_bindings.organization_id",
                "im_installation_bindings.installation_id",
                "im_installation_bindings.id",
            ],
            name="fk_im_inbox_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_inbox_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_im_inbox_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "subject_user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_inbox_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_inbox_messages_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["turns.id"],
            name=op.f("fk_im_inbox_messages_turn_id_turns"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_inbox_messages")),
        sa.UniqueConstraint("installation_id", "vendor_event_id", name="uq_im_inbox_vendor_event"),
        sa.UniqueConstraint("run_id", name="uq_im_inbox_run"),
        sa.UniqueConstraint("turn_id", name="uq_im_inbox_turn"),
    )
    op.create_index(
        op.f("ix_im_inbox_messages_organization_id"),
        "im_inbox_messages",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "im_conversation_bindings",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_digest", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
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
            "length(conversation_digest) = 64",
            name=op.f("ck_im_conversation_bindings_conversation_digest_length"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_conversation_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "subject_user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_conversation_subject",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_im_conversation_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_conversation_bindings_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["threads.id"],
            name=op.f("fk_im_conversation_bindings_thread_id_threads"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_conversation_bindings")),
        sa.UniqueConstraint(
            "installation_id",
            "subject_user_id",
            "conversation_digest",
            name="uq_im_conversation_scope",
        ),
        sa.UniqueConstraint("thread_id", name="uq_im_conversation_thread"),
        sa.UniqueConstraint("workspace_id", name="uq_im_conversation_workspace"),
    )
    op.create_index(
        op.f("ix_im_conversation_bindings_organization_id"),
        "im_conversation_bindings",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE im_installations, im_installation_bindings, im_inbox_messages, "
                "im_conversation_bindings "
                "IN ACCESS EXCLUSIVE MODE"
            )
        )
    for name in (
        "im_installations",
        "im_installation_bindings",
        "im_inbox_messages",
        "im_conversation_bindings",
    ):
        table = sa.table(name, sa.column("id"))
        if connection.scalar(sa.select(table.c.id).limit(1)) is not None:
            raise RuntimeError("Refusing to drop a populated IM admission ledger")
    op.drop_table("im_conversation_bindings")
    op.drop_table("im_inbox_messages")
    op.drop_table("im_installation_bindings")
    op.drop_table("im_installations")
