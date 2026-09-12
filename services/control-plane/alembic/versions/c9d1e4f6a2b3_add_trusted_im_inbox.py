"""add trusted IM installations and durable inbox

Revision ID: c9d1e4f6a2b3
Revises: a88f69d7c8a0
Create Date: 2026-09-08 13:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d1e4f6a2b3"
down_revision: str | None = "a88f69d7c8a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sha256_hex_check(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return f"length({column}) = 64 AND length({remainder}) = 0"


def upgrade() -> None:
    for column in (
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("installation_id", sa.String(length=255), nullable=True),
        sa.Column("corp_id", sa.String(length=255), nullable=True),
        sa.Column("app_key", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    ):
        op.add_column("im_installations", column)
    op.alter_column("im_installations", "provider", nullable=True)
    op.alter_column("im_installations", "external_corp_id", nullable=True)
    op.alter_column("im_installations", "external_app_id", nullable=True)
    op.alter_column("im_installations", "connector_id", nullable=True)
    op.alter_column("im_installations", "adapter_principal_id", nullable=True)
    op.alter_column("im_installations", "status", nullable=True)
    op.alter_column("im_installations", "verification_source", nullable=True)
    # Preserve the referenced unique index used by all historical composite FKs.
    # Renaming the constraint keeps those dependencies valid while matching the
    # current metadata name.
    op.execute(
        "ALTER TABLE im_installations RENAME CONSTRAINT uq_im_installations_org_id "
        "TO uq_im_installations_organization_id_id"
    )
    for constraint in (
        op.f("ck_im_installations_installation_identity"),
        op.f("ck_im_installations_valid_installation_provider"),
        op.f("ck_im_installations_valid_installation_status"),
    ):
        op.drop_constraint(constraint, "im_installations", type_="check")
    op.execute(
        "UPDATE im_installations SET channel = provider, "
        "installation_id = external_corp_id || ':' || external_app_id, "
        "corp_id = external_corp_id, app_key = external_app_id, "
        "active = (status = 'ACTIVE')"
    )
    op.create_check_constraint(
        op.f("ck_im_installations_valid_im_installation_provider"),
        "im_installations",
        "provider IS NULL OR provider IN ('dingtalk', 'feishu', 'wecom')",
    )
    op.create_check_constraint(
        op.f("ck_im_installations_valid_im_installation_status"),
        "im_installations",
        "status IS NULL OR status IN ('ACTIVE', 'REVOKED')",
    )
    op.create_unique_constraint(
        "uq_im_installations_channel_installation",
        "im_installations",
        ["channel", "installation_id"],
    )
    op.create_unique_constraint(
        "uq_im_installations_channel_corp_app",
        "im_installations",
        ["channel", "corp_id", "app_key"],
    )
    op.create_check_constraint(
        op.f("ck_im_installations_nonempty_im_installation_channel"),
        "im_installations",
        "channel IS NULL OR length(trim(channel)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_im_installations_nonempty_im_installation_id"),
        "im_installations",
        "installation_id IS NULL OR length(trim(installation_id)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_im_installations_nonempty_im_installation_corp"),
        "im_installations",
        "corp_id IS NULL OR length(trim(corp_id)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_im_installations_nonempty_im_installation_app_key"),
        "im_installations",
        "app_key IS NULL OR length(trim(app_key)) > 0",
    )

    op.add_column("im_principal_bindings", sa.Column("installation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_im_bindings_org_installation",
        "im_principal_bindings",
        "im_installations",
        ["organization_id", "installation_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_im_principal_bindings_org_channel_sender", "im_principal_bindings", type_="unique"
    )
    op.create_index(
        "uq_im_principal_bindings_legacy_sender",
        "im_principal_bindings",
        ["organization_id", "channel", "sender_id"],
        unique=True,
        postgresql_where=sa.text("installation_id IS NULL"),
    )
    op.create_unique_constraint(
        "uq_im_bindings_installation_sender",
        "im_principal_bindings",
        ["installation_id", "sender_id"],
    )
    op.create_index(
        "ix_im_principal_bindings_installation_id", "im_principal_bindings", ["installation_id"]
    )

    op.create_table(
        "im_conversation_audiences",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            "length(trim(conversation_id)) > 0",
            name=op.f("ck_im_conversation_audiences_nonempty_im_audience_conversation"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_conversation_audiences_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_conversation_audiences_org_installation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_im_conversation_audiences_org_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_conversation_audiences_org_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_conversation_audiences")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_im_conversation_audiences_organization_id_id"
        ),
        sa.UniqueConstraint(
            "installation_id",
            "conversation_id",
            name="uq_im_conversation_audiences_installation_conversation",
        ),
    )
    op.create_index(
        op.f("ix_im_conversation_audiences_organization_id"),
        "im_conversation_audiences",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_conversation_audiences_installation_id"),
        "im_conversation_audiences",
        ["installation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_conversation_audiences_workspace_id"),
        "im_conversation_audiences",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "im_inbox_events",
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("audience_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_by", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("vendor_event_id", sa.String(length=500), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("sender_id", sa.String(length=255), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("rejected_code", sa.String(length=100), nullable=True),
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
            _sha256_hex_check("payload_fingerprint"),
            name=op.f("ck_im_inbox_events_im_inbox_events_payload_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "length(trim(channel)) > 0", name=op.f("ck_im_inbox_events_nonempty_im_inbox_channel")
        ),
        sa.CheckConstraint(
            "length(trim(vendor_event_id)) > 0",
            name=op.f("ck_im_inbox_events_nonempty_im_inbox_vendor_event"),
        ),
        sa.CheckConstraint(
            "length(trim(sender_id)) > 0", name=op.f("ck_im_inbox_events_nonempty_im_inbox_sender")
        ),
        sa.CheckConstraint(
            "length(trim(conversation_id)) > 0",
            name=op.f("ck_im_inbox_events_nonempty_im_inbox_conversation"),
        ),
        sa.CheckConstraint(
            "length(trim(text)) > 0", name=op.f("ck_im_inbox_events_nonempty_im_inbox_text")
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'ACCEPTED', 'REJECTED')",
            name=op.f("ck_im_inbox_events_valid_im_inbox_status"),
        ),
        sa.CheckConstraint(
            "intent IN ('QUERY', 'SUMMARY', 'ANALYSIS', 'CREATE')",
            name=op.f("ck_im_inbox_events_valid_im_intent"),
        ),
        sa.CheckConstraint(
            "(status = 'ACCEPTED' AND run_id IS NOT NULL AND rejected_code IS NULL) OR "
            "(status IN ('PENDING', 'PROCESSING') AND run_id IS NULL "
            "AND rejected_code IS NULL) OR (status = 'REJECTED' AND run_id IS NULL "
            "AND rejected_code IS NOT NULL)",
            name=op.f("ck_im_inbox_events_im_inbox_completion_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_inbox_events_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_inbox_events_org_installation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "binding_id"],
            ["im_principal_bindings.organization_id", "im_principal_bindings.id"],
            name="fk_im_inbox_events_org_binding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "accepted_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_inbox_events_org_acceptor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "audience_id"],
            ["im_conversation_audiences.organization_id", "im_conversation_audiences.id"],
            name="fk_im_inbox_events_org_audience",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_im_inbox_events_org_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_inbox_events")),
        sa.UniqueConstraint("organization_id", "run_id", name="uq_im_inbox_events_org_run"),
        sa.UniqueConstraint(
            "installation_id", "vendor_event_id", name="uq_im_inbox_installation_vendor_event"
        ),
    )
    op.create_index(
        op.f("ix_im_inbox_events_organization_id"),
        "im_inbox_events",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_inbox_events_installation_id"),
        "im_inbox_events",
        ["installation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_inbox_events_binding_id"), "im_inbox_events", ["binding_id"], unique=False
    )
    op.create_index(
        op.f("ix_im_inbox_events_audience_id"), "im_inbox_events", ["audience_id"], unique=False
    )
    op.create_index(op.f("ix_im_inbox_events_status"), "im_inbox_events", ["status"], unique=False)
    op.create_index(
        op.f("ix_im_inbox_events_lease_expires_at"),
        "im_inbox_events",
        ["lease_expires_at"],
        unique=False,
    )
    op.create_index(op.f("ix_im_inbox_events_run_id"), "im_inbox_events", ["run_id"], unique=False)

    op.add_column(
        "im_deliveries", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "im_deliveries", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "im_deliveries",
        sa.Column("reconciliation_required_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_im_deliveries_next_attempt_at"), "im_deliveries", ["next_attempt_at"], unique=False
    )
    op.create_index(
        op.f("ix_im_deliveries_reconciliation_required_at"),
        "im_deliveries",
        ["reconciliation_required_at"],
        unique=False,
    )
    op.drop_constraint(op.f("ck_im_deliveries_valid_status"), "im_deliveries", type_="check")
    op.create_check_constraint(
        op.f("ck_im_deliveries_valid_status"),
        "im_deliveries",
        "status IN ('PENDING', 'UNKNOWN', 'SENT', 'FAILED')",
    )

    op.execute("""
        CREATE FUNCTION obsion_guard_im_inbox_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PENDING' OR NEW.attempt_count <> 0
                   OR NEW.lease_owner IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
                   OR NEW.run_id IS NOT NULL OR NEW.processed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'IM Inbox must begin pending' USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM im_principal_bindings b
                    JOIN im_installations i ON i.id = b.installation_id
                    WHERE b.id = NEW.binding_id AND b.organization_id = NEW.organization_id
                      AND b.installation_id = NEW.installation_id AND b.channel = NEW.channel
                      AND b.sender_id = NEW.sender_id AND b.active AND i.active
                      AND b.user_id::text = NEW.metadata->>'subject_user_id'
                ) THEN
                    RAISE EXCEPTION 'IM Inbox binding does not match installation and subject'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.audience_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM im_conversation_audiences a WHERE a.id = NEW.audience_id
                      AND a.installation_id = NEW.installation_id
                      AND a.conversation_id = NEW.conversation_id AND a.active
                ) THEN
                    RAISE EXCEPTION 'IM Inbox audience does not match installation'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.id, NEW.organization_id, NEW.installation_id, NEW.binding_id,
                   NEW.audience_id, NEW.accepted_by, NEW.channel, NEW.vendor_event_id,
                   NEW.payload_fingerprint, NEW.sender_id, NEW.conversation_id,
                   NEW.is_group, NEW.intent, NEW.text, NEW.metadata::jsonb, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.organization_id, OLD.installation_id, OLD.binding_id,
                   OLD.audience_id, OLD.accepted_by, OLD.channel, OLD.vendor_event_id,
                   OLD.payload_fingerprint, OLD.sender_id, OLD.conversation_id,
                   OLD.is_group, OLD.intent, OLD.text, OLD.metadata::jsonb, OLD.created_at) THEN
                RAISE EXCEPTION 'IM Inbox acceptance is immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.status IN ('ACCEPTED', 'REJECTED') THEN
                RAISE EXCEPTION 'IM Inbox terminal outcome is immutable' USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'PROCESSING' THEN
                IF NEW.attempt_count = OLD.attempt_count + 1 THEN
                    IF OLD.status = 'PROCESSING' AND OLD.lease_expires_at > clock_timestamp() THEN
                        RAISE EXCEPTION 'IM Inbox lease is still active' USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.attempt_count = OLD.attempt_count AND OLD.status = 'PROCESSING'
                      AND NEW.lease_owner = OLD.lease_owner THEN
                    IF OLD.lease_expires_at <= clock_timestamp() THEN
                        RAISE EXCEPTION 'IM Inbox lease has expired' USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'IM Inbox claim generation is invalid' USING ERRCODE = '23514';
                END IF;
                IF NEW.lease_owner IS NULL OR NEW.lease_expires_at IS NULL THEN
                    RAISE EXCEPTION 'IM Inbox processing requires lease' USING ERRCODE = '23514';
                END IF;
            ELSE
                IF OLD.status <> 'PROCESSING' OR NEW.attempt_count <> OLD.attempt_count
                   OR OLD.lease_expires_at <= clock_timestamp()
                   OR NEW.lease_owner IS NOT NULL OR NEW.lease_expires_at IS NOT NULL THEN
                    RAISE EXCEPTION 'IM Inbox completion requires current claim'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER im_inbox_update_guard BEFORE INSERT OR UPDATE ON im_inbox_events
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_im_inbox_update();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER im_inbox_update_guard ON im_inbox_events")
    op.execute("DROP FUNCTION obsion_guard_im_inbox_update()")
    op.drop_constraint(op.f("ck_im_deliveries_valid_status"), "im_deliveries", type_="check")
    op.create_check_constraint(
        op.f("ck_im_deliveries_valid_status"),
        "im_deliveries",
        # The predecessor already supports UNKNOWN (a82c03d14e25). Preserve it
        # until that revision's explicit reconciliation barrier is reached.
        "status IN ('PENDING', 'SENT', 'FAILED', 'UNKNOWN')",
    )
    op.drop_index(op.f("ix_im_deliveries_reconciliation_required_at"), table_name="im_deliveries")
    op.drop_index(op.f("ix_im_deliveries_next_attempt_at"), table_name="im_deliveries")
    op.drop_column("im_deliveries", "reconciliation_required_at")
    op.drop_column("im_deliveries", "last_attempt_at")
    op.drop_column("im_deliveries", "next_attempt_at")
    op.drop_table("im_inbox_events")
    op.drop_table("im_conversation_audiences")
    op.drop_index("ix_im_principal_bindings_installation_id", table_name="im_principal_bindings")
    op.drop_constraint(
        "fk_im_bindings_org_installation", "im_principal_bindings", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_im_bindings_installation_sender", "im_principal_bindings", type_="unique"
    )
    op.drop_index("uq_im_principal_bindings_legacy_sender", table_name="im_principal_bindings")
    op.create_unique_constraint(
        "uq_im_principal_bindings_org_channel_sender",
        "im_principal_bindings",
        ["organization_id", "channel", "sender_id"],
    )
    op.drop_column("im_principal_bindings", "installation_id")
    for constraint in (
        "uq_im_installations_channel_corp_app",
        "uq_im_installations_channel_installation",
    ):
        op.drop_constraint(constraint, "im_installations", type_="unique")
    for constraint in (
        op.f("ck_im_installations_nonempty_im_installation_app_key"),
        op.f("ck_im_installations_nonempty_im_installation_corp"),
        op.f("ck_im_installations_nonempty_im_installation_id"),
        op.f("ck_im_installations_nonempty_im_installation_channel"),
        op.f("ck_im_installations_valid_im_installation_provider"),
        op.f("ck_im_installations_valid_im_installation_status"),
    ):
        op.drop_constraint(constraint, "im_installations", type_="check")
    for column in ("active", "app_key", "corp_id", "installation_id", "channel"):
        op.drop_column("im_installations", column)
    for column in (
        "provider",
        "external_corp_id",
        "external_app_id",
        "connector_id",
        "adapter_principal_id",
        "status",
        "verification_source",
    ):
        op.alter_column("im_installations", column, nullable=False)
    op.execute(
        "ALTER TABLE im_installations RENAME CONSTRAINT uq_im_installations_organization_id_id "
        "TO uq_im_installations_org_id"
    )
    op.create_check_constraint(
        op.f("ck_im_installations_valid_installation_provider"),
        "im_installations",
        "provider IN ('dingtalk', 'feishu', 'wecom')",
    )
    op.create_check_constraint(
        op.f("ck_im_installations_valid_installation_status"),
        "im_installations",
        "status IN ('ACTIVE', 'REVOKED')",
    )
    op.create_check_constraint(
        op.f("ck_im_installations_installation_identity"),
        "im_installations",
        "length(trim(external_corp_id)) > 0 AND length(trim(external_app_id)) > 0 AND "
        "length(trim(verification_source)) > 0",
    )
