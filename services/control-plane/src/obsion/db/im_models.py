"""Installation-scoped, durable IM admission. No vendor credentials live here."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from obsion.db.base import Base, IdMixin, OrganizationMixin, TimestampMixin
from obsion.db.models import ImInstallation

__all__ = [
    "ImConversationBinding",
    "ImGroupAudience",
    "ImInboxMessage",
    "ImInstallation",
    "ImInstallationBinding",
]


class ImInstallationBinding(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "im_installation_bindings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "installation_id",
            "id",
            name="uq_im_installation_bindings_org_installation_id",
        ),
        UniqueConstraint("installation_id", "sender_id", name="uq_im_installation_sender"),
        ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_installation_bindings_installation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_installation_bindings_user",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_installation_bindings_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(sender_id)) > 0", name="installation_sender_nonempty"),
    )

    installation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sender_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImGroupAudience(Base, IdMixin, OrganizationMixin, TimestampMixin):
    """Operator-verified group audience and its bounded workspace projection.

    The member list is a short-lived snapshot supplied by the management plane;
    it is never inferred from a message sender. A stale snapshot fails closed at
    processing and delivery time.
    """

    __tablename__ = "im_group_audiences"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "conversation_id", name="uq_im_group_audience_conversation"
        ),
        ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_group_audience_installation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_im_group_audience_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_group_audience_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name="valid_im_group_audience_status"),
        CheckConstraint(
            "max_classification IN ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED')",
            name="valid_im_group_audience_classification",
        ),
        CheckConstraint(
            "length(trim(conversation_id)) > 0 AND length(member_fingerprint) = 64",
            name="im_group_audience_identity",
        ),
    )

    installation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(512), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    member_user_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    member_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    max_classification: Mapped[str] = mapped_column(String(16), nullable=False, default="INTERNAL")
    allow_final_answer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_status: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    verification_source: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImConversationBinding(Base, IdMixin, OrganizationMixin, TimestampMixin):
    """Durable IM context identity; workspace names and thread titles are labels only."""

    __tablename__ = "im_conversation_bindings"
    __table_args__ = (
        UniqueConstraint(
            "installation_id",
            "subject_user_id",
            "conversation_digest",
            name="uq_im_conversation_scope",
        ),
        UniqueConstraint("workspace_id", name="uq_im_conversation_workspace"),
        UniqueConstraint("thread_id", name="uq_im_conversation_thread"),
        ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_conversation_installation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "subject_user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_conversation_subject",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_im_conversation_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(conversation_digest) = 64", name="conversation_digest_length"),
    )

    installation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    subject_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    conversation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("threads.id", ondelete="RESTRICT"), nullable=False
    )


class ImInboxMessage(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "im_inbox_messages"
    __table_args__ = (
        UniqueConstraint("installation_id", "vendor_event_id", name="uq_im_inbox_vendor_event"),
        UniqueConstraint("turn_id", name="uq_im_inbox_turn"),
        UniqueConstraint("run_id", name="uq_im_inbox_run"),
        ForeignKeyConstraint(
            ["organization_id", "installation_id"],
            ["im_installations.organization_id", "im_installations.id"],
            name="fk_im_inbox_installation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "installation_id", "binding_id"],
            [
                "im_installation_bindings.organization_id",
                "im_installation_bindings.installation_id",
                "im_installation_bindings.id",
            ],
            name="fk_im_inbox_binding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "subject_user_id"],
            ["users.organization_id", "users.id"],
            name="fk_im_inbox_subject",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_im_inbox_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('RECEIVED', 'PROCESSING', 'PROCESSED')", name="valid_inbox_status"
        ),
        CheckConstraint(
            "(status = 'PROCESSED' AND turn_id IS NOT NULL AND run_id IS NOT NULL "
            "AND processed_at IS NOT NULL) OR "
            "(status IN ('RECEIVED', 'PROCESSING') AND turn_id IS NULL "
            "AND run_id IS NULL AND processed_at IS NULL)",
            name="inbox_task_state",
        ),
        CheckConstraint("length(fingerprint) = 64", name="inbox_fingerprint_length"),
        CheckConstraint(
            "length(trim(vendor_event_id)) > 0 AND length(trim(sender_id)) > 0 "
            "AND length(trim(conversation_id)) > 0",
            name="inbox_identity_nonempty",
        ),
    )

    installation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    vendor_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    subject_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sender_id: Mapped[str] = mapped_column(String(255), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(512), nullable=False)
    conversation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RECEIVED")
    turn_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("turns.id", ondelete="RESTRICT"))
    run_id: Mapped[UUID | None] = mapped_column(Uuid)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
