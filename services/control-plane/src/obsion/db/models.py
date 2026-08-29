from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from obsion.db.base import Base, IdMixin, OrganizationMixin, TimestampMixin
from obsion.db.types import ErrorCodeType
from obsion.domain.enums import (
    ActionApprovalPurpose,
    ActionAttemptStatus,
    ActionStatus,
    ActionType,
    ActorType,
    AnswerPublicationDecision,
    ApprovalStatus,
    ArtifactKind,
    AutomationStatus,
    AutomationStepStatus,
    AutomationTrigger,
    CapabilityTransport,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    EvaluationResultStatus,
    EvaluationTarget,
    EvidenceConflictDisposition,
    EvidenceConflictKind,
    EvidenceConflictSeverity,
    EvidenceRelation,
    EvidenceType,
    MemoryScope,
    MemoryStatus,
    NotificationStatus,
    ObservationValueType,
    RegistryStatus,
    ReviewDecision,
    RiskLevel,
    RunFeedbackRating,
    RunStatus,
    ScheduleMisfirePolicy,
    SideEffect,
    StepKind,
    StepStatus,
    ThreadStatus,
    VerificationOutcome,
    VerificationRuleOutcome,
    VerificationStatus,
    Visibility,
    WorkflowConcurrencyPolicy,
    WorkflowStatus,
    WorkflowStepType,
    WorkspaceDecisionStatus,
    WorkspaceTaskPriority,
    WorkspaceTaskStatus,
)


def enum_type(enum: type, length: int = 32) -> Enum:
    return Enum(
        enum,
        native_enum=False,
        length=length,
        values_callable=lambda values: [x.value for x in values],
    )


def sha256_hex_check(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return f"length({column}) = 64 AND length({remainder}) = 0"


class Organization(Base, IdMixin, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Department(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_departments_organization_id_id"),
        UniqueConstraint("organization_id", "name", name="uq_departments_organization_id_name"),
        ForeignKeyConstraint(
            ["organization_id", "parent_id"],
            ["departments.organization_id", "departments.id"],
            name="fk_departments_org_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(name)) > 0", name="nonempty_department_name"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[UUID | None] = mapped_column(Uuid)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class User(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_users_organization_id_id"),
        UniqueConstraint(
            "organization_id", "external_id", name="uq_users_organization_id_external_id"
        ),
        ForeignKeyConstraint(
            ["organization_id", "department_id"],
            ["departments.organization_id", "departments.id"],
            name="fk_users_org_department",
            ondelete="RESTRICT",
        ),
    )

    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Role(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_roles_organization_id_id"),
        UniqueConstraint("organization_id", "name", name="uq_roles_organization_id_name"),
        CheckConstraint("length(trim(name)) > 0", name="nonempty_role_name"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UserRole(Base, OrganizationMixin, TimestampMixin):
    __tablename__ = "user_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_user_roles_org_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "role_id"],
            ["roles.organization_id", "roles.id"],
            name="fk_user_roles_org_role",
            ondelete="CASCADE",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    role_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AuthSession(Base, IdMixin, OrganizationMixin, TimestampMixin):
    """Opaque, revocable browser session.

    Only a SHA-256 digest is persisted. The bearer token used to establish the
    session and the opaque cookie value are never stored in recoverable form.
    """

    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_auth_sessions_org_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_digest", name="uq_auth_sessions_token_digest"),
        CheckConstraint(sha256_hex_check("token_digest"), name="valid_auth_session_digest"),
    )

    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class Workspace(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_workspaces_organization_id_id"),
        ForeignKeyConstraint(
            ["organization_id", "owner_id"],
            ["users.organization_id", "users.id"],
            name="fk_workspaces_org_owner",
            ondelete="RESTRICT",
        ),
    )

    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False, default=Classification.INTERNAL
    )
    visibility: Mapped[Visibility] = mapped_column(
        enum_type(Visibility), nullable=False, default=Visibility.PRIVATE
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMember(Base, OrganizationMixin):
    __tablename__ = "workspace_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_workspace_members_org_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_workspace_members_org_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_workspace_members_org_creator",
            ondelete="RESTRICT",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    can_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceTask(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "workspace_tasks"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[WorkspaceTaskStatus] = mapped_column(
        enum_type(WorkspaceTaskStatus),
        nullable=False,
        default=WorkspaceTaskStatus.OPEN,
        index=True,
    )
    priority: Mapped[WorkspaceTaskPriority] = mapped_column(
        enum_type(WorkspaceTaskPriority),
        nullable=False,
        default=WorkspaceTaskPriority.NORMAL,
        index=True,
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("version > 0", name="positive_workspace_task_version"),
        CheckConstraint("length(trim(title)) > 0", name="nonempty_workspace_task_title"),
    )


class WorkspaceDecision(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "workspace_decisions"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[WorkspaceDecisionStatus] = mapped_column(
        enum_type(WorkspaceDecisionStatus),
        nullable=False,
        default=WorkspaceDecisionStatus.PROPOSED,
        index=True,
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    source_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), index=True
    )
    supersedes_decision_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("workspace_decisions.id", ondelete="RESTRICT"), index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("current_version > 0", name="positive_workspace_decision_version"),
        CheckConstraint(
            "supersedes_decision_id IS NULL OR supersedes_decision_id <> id",
            name="workspace_decision_cannot_supersede_self",
        ),
    )


class WorkspaceDecisionVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "workspace_decision_versions"

    decision_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workspace_decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    alternatives: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("decision_id", "version"),
        CheckConstraint("version > 0", name="positive_decision_revision"),
        CheckConstraint("length(trim(title)) > 0", name="nonempty_decision_title"),
    )


class Thread(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "threads"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[ThreadStatus] = mapped_column(
        enum_type(ThreadStatus), nullable=False, default=ThreadStatus.ACTIVE
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    parent_thread_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("threads.id", ondelete="SET NULL")
    )
    forked_from_turn_id: Mapped[UUID | None] = mapped_column(Uuid)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Turn(Base, IdMixin, OrganizationMixin):
    __tablename__ = "turns"

    thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    sanitized_input: Mapped[str] = mapped_column(Text, nullable=False)
    context_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attachment_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("thread_id", "ordinal"),)


class Run(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "runs"

    turn_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("turns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[RunStatus] = mapped_column(
        enum_type(RunStatus), nullable=False, default=RunStatus.PENDING, index=True
    )
    agent_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    model_profile_id: Mapped[UUID | None] = mapped_column(Uuid)
    intent: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=120_000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=16_000)
    max_cost_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=10)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    replay_of_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_runs_organization_id_id"),
        CheckConstraint("max_steps > 0", name="positive_max_steps"),
        CheckConstraint("timeout_seconds > 0", name="positive_timeout"),
        CheckConstraint("max_input_tokens > 0", name="positive_max_input_tokens"),
        CheckConstraint("max_output_tokens > 0", name="positive_max_output_tokens"),
        CheckConstraint("max_cost_amount > 0", name="positive_max_cost_amount"),
    )


class RunFeedback(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "run_feedback"
    __table_args__ = (
        UniqueConstraint("organization_id", "run_id", "user_id"),
        CheckConstraint("version > 0", name="positive_run_feedback_version"),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rating: Mapped[RunFeedbackRating] = mapped_column(
        enum_type(RunFeedbackRating), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RunStep(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "run_steps"

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[StepKind] = mapped_column(enum_type(StepKind), nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        enum_type(StepStatus), nullable=False, default=StepStatus.PENDING
    )
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    capability_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    output_ref: Mapped[str | None] = mapped_column(String(500))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))

    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_run_steps_organization_run_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_run_steps_organization_run",
            ondelete="CASCADE",
        ),
    )


class Event(Base, IdMixin, OrganizationMixin):
    __tablename__ = "events"

    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    run_sequence: Mapped[int | None] = mapped_column(Integer)
    causation_id: Mapped[UUID | None] = mapped_column(Uuid)
    correlation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    actor_type: Mapped[ActorType] = mapped_column(enum_type(ActorType), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False, default=Classification.INTERNAL
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("aggregate_type", "aggregate_id", "sequence"),
        UniqueConstraint("run_id", "run_sequence", name="uq_events_run_sequence"),
        CheckConstraint(
            "(run_id IS NULL AND run_sequence IS NULL) OR "
            "(run_id IS NOT NULL AND run_sequence IS NOT NULL)",
            name="event_run_sequence_consistent",
        ),
        CheckConstraint(
            "run_sequence IS NULL OR run_sequence > 0",
            name="positive_event_run_sequence",
        ),
        Index("ix_events_run_sequence", "run_id", "run_sequence"),
    )


class AggregateHead(Base, OrganizationMixin):
    __tablename__ = "aggregate_heads"

    aggregate_type: Mapped[str] = mapped_column(String(60), primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxMessage(Base, IdMixin):
    __tablename__ = "outbox_messages"

    event_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("events.id"), nullable=False, unique=True
    )
    topic: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AppServerRequest(Base, IdMixin, OrganizationMixin):
    __tablename__ = "app_server_requests"

    principal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    method: Mapped[str] = mapped_column(String(120), nullable=False)
    params_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "principal_id",
            "client_request_id",
            name="uq_app_server_request_principal_key",
        ),
        CheckConstraint(
            "length(trim(client_request_id)) > 0",
            name="nonempty_app_server_client_request_id",
        ),
        CheckConstraint(
            "length(params_fingerprint) = 64",
            name="app_server_params_fingerprint_length",
        ),
        CheckConstraint(
            "(response IS NULL AND completed_at IS NULL) OR "
            "(response IS NOT NULL AND completed_at IS NOT NULL)",
            name="app_server_request_completion_consistent",
        ),
        Index(
            "ix_app_server_requests_lookup",
            "organization_id",
            "principal_id",
            "client_request_id",
        ),
    )


class Artifact(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "artifacts"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"), index=True)
    kind: Mapped[ArtifactKind] = mapped_column(enum_type(ArtifactKind), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    inline_content: Mapped[dict | None] = mapped_column(JSON)
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False, default=Classification.INTERNAL
    )
    acl: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RegistryDefinitionMixin:
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[RegistryStatus] = mapped_column(
        enum_type(RegistryStatus), nullable=False, default=RegistryStatus.DRAFT
    )


class AgentDefinition(Base, IdMixin, OrganizationMixin, TimestampMixin, RegistryDefinitionMixin):
    __tablename__ = "agent_definitions"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)


class AgentVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    agent_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SkillDefinition(Base, IdMixin, OrganizationMixin, TimestampMixin, RegistryDefinitionMixin):
    __tablename__ = "skill_definitions"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)


class SkillVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)

    skill_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("skill_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapabilityDefinition(
    Base, IdMixin, OrganizationMixin, TimestampMixin, RegistryDefinitionMixin
):
    __tablename__ = "capability_definitions"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)


class CapabilityVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "capability_versions"
    __table_args__ = (UniqueConstraint("capability_id", "version"),)

    capability_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("capability_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    transport: Mapped[CapabilityTransport] = mapped_column(
        enum_type(CapabilityTransport), nullable=False
    )
    risk_level: Mapped[RiskLevel] = mapped_column(enum_type(RiskLevel), nullable=False)
    side_effect: Mapped[SideEffect] = mapped_column(enum_type(SideEffect), nullable=False)
    permission_action: Mapped[str] = mapped_column(String(200), nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_mapping: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    data_classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False, default=Classification.INTERNAL
    )
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Connector(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[ConnectorStatus] = mapped_column(
        enum_type(ConnectorStatus), nullable=False, default=ConnectorStatus.DRAFT
    )
    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(1024))
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(500))
    declared_grants: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allowed_egress: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_health: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class CapabilityBinding(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "capability_bindings"
    __table_args__ = (UniqueConstraint("capability_version_id", "connector_id", "environment"),)

    capability_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("capability_versions.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_selector: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Policy(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    effect: Mapped[DecisionEffect] = mapped_column(enum_type(DecisionEffect), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    conditions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    obligations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)


class PolicyDecision(Base, IdMixin, OrganizationMixin):
    __tablename__ = "policy_decisions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_policy_decisions_organization_run_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_policy_decisions_organization_run",
        ),
    )

    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"), index=True)
    principal_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    agent_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    capability_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    resource: Mapped[dict] = mapped_column(JSON, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(enum_type(RiskLevel), nullable=False)
    effect: Mapped[DecisionEffect] = mapped_column(enum_type(DecisionEffect), nullable=False)
    matched_policy_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    obligations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reason_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Approval(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "approvals"

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_steps.id"))
    policy_decision_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("policy_decisions.id"), nullable=False
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    approver_constraints: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resume_token_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditRecord(Base, IdMixin, OrganizationMixin):
    __tablename__ = "audit_logs"

    correlation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    actor_type: Mapped[ActorType] = mapped_column(enum_type(ActorType), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(300))
    outcome: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_level: Mapped[RiskLevel | None] = mapped_column(enum_type(RiskLevel))
    policy_decision_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("policy_decisions.id"))
    approval_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("approvals.id"))
    redacted_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Evidence(Base, IdMixin, OrganizationMixin):
    __tablename__ = "evidence"

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_steps.id"))
    evidence_type: Mapped[EvidenceType] = mapped_column(enum_type(EvidenceType), nullable=False)
    source: Mapped[str] = mapped_column(String(240), nullable=False)
    resource: Mapped[str] = mapped_column(String(1024), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_evidence_organization_run_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_evidence_organization_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "step_id"],
            ["run_steps.organization_id", "run_steps.run_id", "run_steps.id"],
            name="fk_evidence_organization_run_step",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="valid_confidence"),
        CheckConstraint(
            sha256_hex_check("content_fingerprint"),
            name="content_fingerprint_sha256",
        ),
    )


class EvidenceObservation(Base, IdMixin, OrganizationMixin):
    """Versioned, typed semantics extracted deterministically from one Evidence."""

    __tablename__ = "evidence_observations"

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    measure: Mapped[str] = mapped_column(String(300), nullable=False)
    value_type: Mapped[ObservationValueType] = mapped_column(
        enum_type(ObservationValueType), nullable=False
    )
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    unit: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(200), nullable=False)
    mapping_version: Mapped[str] = mapped_column(String(160), nullable=False)
    mapping_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_evidence_observations_organization_run_id",
        ),
        UniqueConstraint(
            "evidence_id",
            "ordinal",
            name="uq_evidence_observations_evidence_ordinal",
        ),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "evidence_id",
            "id",
            name="uq_evidence_observations_evidence_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_evidence_observations_organization_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_evidence_observations_evidence",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "value_type IN ('TEXT', 'NUMBER', 'BOOLEAN', 'DATETIME', 'JSON')",
            name="valid_value_type",
        ),
        CheckConstraint("ordinal > 0", name="positive_ordinal"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="valid_confidence",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="valid_interval",
        ),
        CheckConstraint(
            sha256_hex_check("scope_fingerprint"),
            name="scope_fingerprint_sha256",
        ),
        CheckConstraint(
            sha256_hex_check("mapping_fingerprint"),
            name="mapping_fingerprint_sha256",
        ),
        CheckConstraint(
            sha256_hex_check("observation_fingerprint"),
            name="observation_fingerprint_sha256",
        ),
        CheckConstraint(
            "length(trim(subject)) > 0 AND length(trim(measure)) > 0",
            name="nonempty_key",
        ),
        Index(
            "ix_evidence_observations_comparable",
            "organization_id",
            "run_id",
            "subject",
            "measure",
            "unit",
            "environment",
            "definition_version",
        ),
    )


class Claim(Base, IdMixin, OrganizationMixin):
    __tablename__ = "claims"

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_type(VerificationStatus), nullable=False, default=VerificationStatus.PENDING
    )
    critic_notes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "generation", "ordinal"),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_claims_organization_run_id",
        ),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "generation",
            "id",
            name="uq_claims_organization_run_generation_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_claims_organization_run",
            ondelete="CASCADE",
        ),
        CheckConstraint("generation > 0", name="positive_claim_generation"),
        CheckConstraint("ordinal > 0", name="positive_claim_ordinal"),
        CheckConstraint("length(trim(statement)) > 0", name="nonempty_claim_statement"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="valid_confidence"),
    )


class ClaimEvidence(Base, OrganizationMixin):
    __tablename__ = "claim_evidence"

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    claim_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_claim_evidence_organization_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "claim_id"],
            ["claims.organization_id", "claims.run_id", "claims.id"],
            name="fk_claim_evidence_claim",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_claim_evidence_evidence",
            ondelete="CASCADE",
        ),
    )


class VerificationAssessment(Base, IdMixin, OrganizationMixin):
    """Immutable decision over one Claim generation and a frozen Evidence set.

    VERIFIED is only a candidate publication state until the database admission
    trigger seals the aggregate after all child results and conflicts exist.
    """

    __tablename__ = "verification_assessments"

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    verify_step_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[VerificationOutcome] = mapped_column(
        enum_type(VerificationOutcome), nullable=False, index=True
    )
    publication_decision: Mapped[AnswerPublicationDecision] = mapped_column(
        enum_type(AnswerPublicationDecision), nullable=False
    )
    evaluator: Mapped[str] = mapped_column(String(160), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(80), nullable=False)
    route: Mapped[str] = mapped_column(String(80), nullable=False)
    rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ruleset_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    ruleset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_decision_id: Mapped[UUID | None] = mapped_column(Uuid)
    minimum_coverage: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    minimum_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    coverage: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    checks: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    missing_requirements: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    high_conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(120))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "attempt", name="uq_verification_assessments_run_attempt"),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_verification_assessments_organization_run_id",
        ),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "claim_generation",
            "id",
            name="uq_verification_assessments_run_generation_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_verification_assessments_organization_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "verify_step_id"],
            ["run_steps.organization_id", "run_steps.run_id", "run_steps.id"],
            name="fk_verification_assessments_verify_step",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "policy_decision_id"],
            [
                "policy_decisions.organization_id",
                "policy_decisions.run_id",
                "policy_decisions.id",
            ],
            name="fk_verification_assessments_policy_decision",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "outcome IN ('VERIFIED', 'PARTIAL', 'REJECTED', 'ERROR')",
            name="valid_outcome",
        ),
        CheckConstraint(
            "publication_decision IN ('PUBLISH', 'PUBLISH_MASKED', 'AWAIT_APPROVAL', 'WITHHOLD')",
            name="valid_publication_decision",
        ),
        CheckConstraint("attempt > 0", name="positive_attempt"),
        CheckConstraint("claim_generation > 0", name="positive_claim_generation"),
        CheckConstraint(
            "minimum_coverage >= 0 AND minimum_coverage <= 1",
            name="minimum_coverage_range",
        ),
        CheckConstraint(
            "minimum_confidence >= 0 AND minimum_confidence <= 1",
            name="minimum_confidence_range",
        ),
        CheckConstraint(
            "coverage >= 0 AND coverage <= 1",
            name="coverage_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
        CheckConstraint(
            sha256_hex_check("ruleset_fingerprint"),
            name="ruleset_fingerprint_sha256",
        ),
        CheckConstraint(
            sha256_hex_check("input_fingerprint"),
            name="input_fingerprint_sha256",
        ),
        CheckConstraint("high_conflict_count >= 0", name="nonnegative_conflicts"),
        CheckConstraint("duration_ms >= 0", name="nonnegative_duration"),
        CheckConstraint(
            "outcome <> 'VERIFIED' OR ("
            "publication_decision IN ('PUBLISH', 'PUBLISH_MASKED') AND "
            "coverage >= minimum_coverage AND confidence >= minimum_confidence AND "
            "high_conflict_count = 0 AND error_code IS NULL AND "
            "json_array_length(missing_requirements) = 0)",
            name="verified_assessment_admissible",
        ),
        CheckConstraint(
            "publication_decision NOT IN ('PUBLISH', 'PUBLISH_MASKED') OR "
            "(outcome = 'VERIFIED' AND policy_decision_id IS NOT NULL)",
            name="publication_requires_verified",
        ),
        CheckConstraint(
            "(outcome = 'ERROR' AND error_code IS NOT NULL) OR "
            "(outcome <> 'ERROR' AND error_code IS NULL)",
            name="verification_error_consistent",
        ),
    )


class ClaimVerificationResult(Base, IdMixin, OrganizationMixin):
    __tablename__ = "claim_verification_results"

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    assessment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    claim_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[VerificationOutcome] = mapped_column(
        enum_type(VerificationOutcome), nullable=False
    )
    coverage: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    checks: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    material: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "claim_id",
            name="uq_claim_verification_results_assessment_claim",
        ),
        UniqueConstraint(
            "assessment_id",
            "ordinal",
            name="uq_claim_verification_results_assessment_ordinal",
        ),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_claim_verification_results_organization_run_id",
        ),
        UniqueConstraint(
            "organization_id",
            "run_id",
            "assessment_id",
            "id",
            name="uq_claim_verification_results_assessment_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_claim_verification_results_organization_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "claim_generation", "assessment_id"],
            [
                "verification_assessments.organization_id",
                "verification_assessments.run_id",
                "verification_assessments.claim_generation",
                "verification_assessments.id",
            ],
            name="fk_claim_verification_results_assessment",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "claim_generation", "claim_id"],
            [
                "claims.organization_id",
                "claims.run_id",
                "claims.generation",
                "claims.id",
            ],
            name="fk_claim_verification_results_claim",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "outcome IN ('VERIFIED', 'PARTIAL', 'REJECTED', 'ERROR')",
            name="valid_outcome",
        ),
        CheckConstraint("ordinal > 0", name="positive_ordinal"),
        CheckConstraint(
            "coverage >= 0 AND coverage <= 1",
            name="coverage_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_range",
        ),
    )


class VerificationEvidenceLink(Base, IdMixin, OrganizationMixin):
    __tablename__ = "verification_evidence_links"

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    assessment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    claim_result_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    evidence_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    observation_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    rule: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_outcome: Mapped[VerificationRuleOutcome] = mapped_column(
        enum_type(VerificationRuleOutcome), nullable=False
    )
    relation: Mapped[EvidenceRelation] = mapped_column(enum_type(EvidenceRelation), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "claim_result_id",
            "evidence_id",
            "rule",
            name="uq_verification_evidence_links_result_evidence_rule",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "assessment_id"],
            [
                "verification_assessments.organization_id",
                "verification_assessments.run_id",
                "verification_assessments.id",
            ],
            name="fk_verification_evidence_links_assessment",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "assessment_id", "claim_result_id"],
            [
                "claim_verification_results.organization_id",
                "claim_verification_results.run_id",
                "claim_verification_results.assessment_id",
                "claim_verification_results.id",
            ],
            name="fk_verification_evidence_links_claim_result",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_verification_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id", "observation_id"],
            [
                "evidence_observations.organization_id",
                "evidence_observations.run_id",
                "evidence_observations.evidence_id",
                "evidence_observations.id",
            ],
            name="fk_verification_evidence_links_observation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "rule_outcome IN ('PASSED', 'FAILED', 'INDETERMINATE', 'NOT_APPLICABLE')",
            name="valid_rule_outcome",
        ),
        CheckConstraint(
            "relation IN ('SUPPORTS', 'CONTRADICTS', 'NEUTRAL')",
            name="valid_relation",
        ),
        CheckConstraint("length(trim(rule)) > 0", name="nonempty_rule"),
        CheckConstraint(
            sha256_hex_check("source_fingerprint"),
            name="source_fingerprint_sha256",
        ),
    )


class EvidenceConflict(Base, IdMixin, OrganizationMixin):
    __tablename__ = "evidence_conflicts"

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    assessment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    left_evidence_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    right_evidence_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    left_observation_id: Mapped[UUID | None] = mapped_column(Uuid)
    right_observation_id: Mapped[UUID | None] = mapped_column(Uuid)
    kind: Mapped[EvidenceConflictKind] = mapped_column(
        enum_type(EvidenceConflictKind), nullable=False
    )
    severity: Mapped[EvidenceConflictSeverity] = mapped_column(
        enum_type(EvidenceConflictSeverity), nullable=False, index=True
    )
    disposition: Mapped[EvidenceConflictDisposition] = mapped_column(
        enum_type(EvidenceConflictDisposition),
        nullable=False,
        default=EvidenceConflictDisposition.UNRESOLVED,
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    measure: Mapped[str] = mapped_column(String(300), nullable=False)
    unit: Mapped[str] = mapped_column(String(120), nullable=False)
    environment: Mapped[str] = mapped_column(String(120), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    conflict_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "conflict_fingerprint",
            name="uq_evidence_conflicts_assessment_fingerprint",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "assessment_id"],
            [
                "verification_assessments.organization_id",
                "verification_assessments.run_id",
                "verification_assessments.id",
            ],
            name="fk_evidence_conflicts_assessment",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "left_evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_evidence_conflicts_left_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "right_evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_evidence_conflicts_right_evidence",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "left_evidence_id", "left_observation_id"],
            [
                "evidence_observations.organization_id",
                "evidence_observations.run_id",
                "evidence_observations.evidence_id",
                "evidence_observations.id",
            ],
            name="fk_evidence_conflicts_left_observation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id", "right_evidence_id", "right_observation_id"],
            [
                "evidence_observations.organization_id",
                "evidence_observations.run_id",
                "evidence_observations.evidence_id",
                "evidence_observations.id",
            ],
            name="fk_evidence_conflicts_right_observation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('VALUE', 'TEMPORAL', 'DEFINITION', 'SCOPE')",
            name="valid_kind",
        ),
        CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="valid_severity",
        ),
        CheckConstraint(
            "disposition IN ('UNRESOLVED', 'EXPLAINED', 'DUPLICATE')",
            name="valid_disposition",
        ),
        CheckConstraint(
            "left_evidence_id <> right_evidence_id",
            name="distinct_evidence",
        ),
        CheckConstraint(
            "left_observation_id IS NULL OR right_observation_id IS NULL OR "
            "left_observation_id <> right_observation_id",
            name="distinct_observations",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="valid_interval",
        ),
        CheckConstraint(
            sha256_hex_check("scope_fingerprint"),
            name="scope_fingerprint_sha256",
        ),
        CheckConstraint(
            sha256_hex_check("conflict_fingerprint"),
            name="conflict_fingerprint_sha256",
        ),
        CheckConstraint(
            "length(trim(subject)) > 0 AND length(trim(measure)) > 0",
            name="nonempty_key",
        ),
    )


class ModelProfile(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "model_profiles"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    requirements: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    routing_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ModelEndpoint(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "model_endpoints"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    model_id: Mapped[str] = mapped_column(String(240), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(500))
    region: Mapped[str | None] = mapped_column(String(120))
    classifications: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    limits: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ModelProfileEndpoint(Base):
    __tablename__ = "model_profile_endpoints"

    profile_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_endpoints.id", ondelete="CASCADE"), primary_key=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class ModelCall(Base, IdMixin, OrganizationMixin):
    __tablename__ = "model_calls"

    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"), index=True)
    step_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_steps.id"))
    operation: Mapped[str] = mapped_column(String(40), nullable=False, default="CHAT")
    profile_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("model_profiles.id"), nullable=False)
    endpoint_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_endpoints.id"), nullable=False
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Memory(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "memories"
    __table_args__ = (UniqueConstraint("organization_id", "scope", "owner_ref", "dedupe_key"),)

    scope: Mapped[MemoryScope] = mapped_column(enum_type(MemoryScope), nullable=False)
    owner_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity: Mapped[Classification] = mapped_column(enum_type(Classification), nullable=False)
    status: Mapped[MemoryStatus] = mapped_column(
        enum_type(MemoryStatus), nullable=False, default=MemoryStatus.CANDIDATE
    )
    policy_decision_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("policy_decisions.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class RunMemorySnapshot(Base, IdMixin, OrganizationMixin):
    """Immutable memory context pinned to one Run for inspection and replay."""

    __tablename__ = "run_memory_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "memory_id", name="uq_run_memory_snapshots_memory"),
        UniqueConstraint("run_id", "ordinal", name="uq_run_memory_snapshots_ordinal"),
        CheckConstraint("ordinal > 0", name="positive_memory_snapshot_ordinal"),
        Index("ix_run_memory_snapshots_run", "organization_id", "run_id", "ordinal"),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memory_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    principal_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[MemoryScope] = mapped_column(enum_type(MemoryScope), nullable=False)
    owner_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity: Mapped[Classification] = mapped_column(enum_type(Classification), nullable=False)
    policy_decision_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("policy_decisions.id"), nullable=False
    )
    memory_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunConversationSnapshot(Base, IdMixin, OrganizationMixin):
    """Immutable prior Thread context pinned to one Run for model use and replay."""

    __tablename__ = "run_conversation_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "source_turn_id", name="uq_run_conversation_snapshots_turn"),
        UniqueConstraint("run_id", "ordinal", name="uq_run_conversation_snapshots_ordinal"),
        CheckConstraint("ordinal > 0", name="positive_conversation_ordinal"),
        CheckConstraint(
            "length(content_fingerprint) = 64",
            name="conversation_fingerprint_length",
        ),
        Index(
            "ix_run_conversation_snapshots_run",
            "organization_id",
            "run_id",
            "ordinal",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_thread_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("threads.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_turn_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("turns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="RESTRICT"), index=True
    )
    source_artifact_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="RESTRICT"), index=True
    )
    source_principal_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    user_content: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_content: Mapped[str | None] = mapped_column(Text)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Document(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("organization_id", "source", "external_id"),)

    source: Mapped[str] = mapped_column(String(200), nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    acl: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    document_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    content_ref: Mapped[str | None] = mapped_column(String(1024))
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(120), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentChunk(Base, IdMixin, OrganizationMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_version_id", "ordinal"),)

    document_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    acl: Mapped[dict] = mapped_column(JSON, nullable=False)
    embedding_ref: Mapped[str | None] = mapped_column(String(500))
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1536))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Index(
    "ix_document_chunks_embedding_hnsw",
    DocumentChunk.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_where=DocumentChunk.embedding.is_not(None),
).ddl_if(dialect="postgresql")
Index(
    "ix_document_chunks_content_fts",
    text("to_tsvector('simple'::regconfig, content)"),
    postgresql_using="gin",
    _table=cast(Table, DocumentChunk.__table__),
).ddl_if(dialect="postgresql")


class DocumentChunkGrant(Base, OrganizationMixin):
    __tablename__ = "document_chunk_grants"

    chunk_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("document_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    effect: Mapped[str] = mapped_column(String(12), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    subject_value: Mapped[str] = mapped_column(String(300), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("effect IN ('ALLOW', 'DENY')", name="valid_effect"),
        CheckConstraint(
            "subject_type IN ('USER', 'ROLE', 'DEPARTMENT', 'ORGANIZATION')",
            name="valid_subject_type",
        ),
        Index(
            "ix_document_chunk_grants_subject",
            "organization_id",
            "subject_type",
            "subject_value",
            "effect",
        ),
    )


class DataSource(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dialect: Mapped[str] = mapped_column(String(80), nullable=False)
    connector_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("connectors.id"), nullable=False)
    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    query_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class DataTable(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "data_tables"
    __table_args__ = (UniqueConstraint("data_source_id", "schema_name", "table_name"),)

    data_source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str] = mapped_column(String(200), nullable=False)
    table_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    row_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class DataColumn(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "data_columns"
    __table_args__ = (UniqueConstraint("table_id", "name"),)

    table_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_tables.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    data_type: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False
    )
    mask_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Metric(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    time_column: Mapped[str] = mapped_column(String(200), nullable=False)
    source_table_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_tables.id"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    synonyms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    validated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Dimension(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "dimensions"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    source_table_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_tables.id"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    synonyms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class SemanticEntity(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "semantic_entities"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_key_expression: Mapped[str] = mapped_column(Text, nullable=False)
    source_table_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("data_tables.id"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(200), nullable=False)


class SemanticRelation(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "semantic_relations"

    source_entity_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("semantic_entities.id"), nullable=False
    )
    target_entity_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("semantic_entities.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    join_expression: Mapped[str] = mapped_column(Text, nullable=False)
    cardinality: Mapped[str] = mapped_column(String(80), nullable=False)


class BusinessRule(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "business_rules"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    expression: Mapped[dict] = mapped_column(JSON, nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TimeDefinition(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "time_definitions"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(120), nullable=False)
    grains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    fiscal_calendar: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)


class SemanticSynonym(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "semantic_synonyms"
    __table_args__ = (
        UniqueConstraint("organization_id", "locale", "term", "target_type", "target_id"),
        CheckConstraint(
            "target_type IN ('METRIC', 'DIMENSION', 'ENTITY', 'RULE')",
            name="valid_target_type",
        ),
    )

    term: Mapped[str] = mapped_column(String(300), nullable=False)
    locale: Mapped[str] = mapped_column(String(40), nullable=False, default="und")
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)


class EvaluationDataset(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    domain: Mapped[str] = mapped_column(String(100), nullable=False)


class EvaluationCase(Base, IdMixin, OrganizationMixin):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("dataset_id", "external_id", "version"),)

    dataset_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator: Mapped[EvaluationTarget] = mapped_column(enum_type(EvaluationTarget), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected: Mapped[dict] = mapped_column(JSON, nullable=False)
    fixtures: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationRun(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "evaluation_runs"

    dataset_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_datasets.id"), nullable=False
    )
    agent_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("agent_versions.id"), nullable=False
    )
    model_profile_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("model_profiles.id"), nullable=False
    )
    application_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    baseline_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("evaluation_runs.id"))
    dataset_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCaseResult(Base, IdMixin, OrganizationMixin):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id",
            "evaluation_case_id",
            name="uq_evaluation_case_results_evaluation_run_id",
        ),
        UniqueConstraint(
            "evaluation_run_id",
            "ordinal",
            name="uq_evaluation_case_results_evaluation_run_id_ordinal",
        ),
        CheckConstraint("duration_ms >= 0", name="non_negative_duration"),
    )

    evaluation_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluation_case_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_cases.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator: Mapped[EvaluationTarget] = mapped_column(enum_type(EvaluationTarget), nullable=False)
    status: Mapped[EvaluationResultStatus] = mapped_column(
        enum_type(EvaluationResultStatus), nullable=False
    )
    case_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    checks: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    observed: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromptDefinition(Base, IdMixin, OrganizationMixin, TimestampMixin, RegistryDefinitionMixin):
    __tablename__ = "prompt_definitions"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)


class PromptVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("prompt_id", "version"),)

    prompt_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("prompt_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables_schema: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SecretReference(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "secret_references"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encrypted_envelope: Mapped[bytes | None] = mapped_column(LargeBinary)


class WorkflowDefinition(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("organization_id", "workspace_id", "name"),
        CheckConstraint("max_concurrency > 0", name="positive_max_concurrency"),
        CheckConstraint("timeout_seconds > 0", name="positive_workflow_timeout"),
        CheckConstraint(
            "active_version IS NULL OR active_version > 0",
            name="positive_active_workflow_version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[WorkflowStatus] = mapped_column(
        enum_type(WorkflowStatus), nullable=False, default=WorkflowStatus.DRAFT, index=True
    )
    owner_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    active_version: Mapped[int | None] = mapped_column(Integer)
    concurrency_policy: Mapped[WorkflowConcurrencyPolicy] = mapped_column(
        enum_type(WorkflowConcurrencyPolicy),
        nullable=False,
        default=WorkflowConcurrencyPolicy.FORBID,
    )
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    notify_on_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False, default=Classification.INTERNAL
    )


class WorkflowVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)

    workflow_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowSchedule(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "workflow_schedules"
    __table_args__ = (
        UniqueConstraint("workflow_id", "name"),
        CheckConstraint("misfire_grace_seconds >= 0", name="nonnegative_misfire_grace"),
        Index("ix_workflow_schedules_due", "enabled", "next_fire_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_versions.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    misfire_policy: Mapped[ScheduleMisfirePolicy] = mapped_column(
        enum_type(ScheduleMisfirePolicy),
        nullable=False,
        default=ScheduleMisfirePolicy.FIRE_ONCE,
    )
    misfire_grace_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    owner_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    next_fire_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)


class AutomationExecution(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "automation_executions"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key"),
        UniqueConstraint("schedule_id", "scheduled_for"),
        CheckConstraint("max_duration_seconds > 0", name="positive_automation_duration"),
        Index("ix_automation_executions_claim", "status", "lease_expires_at", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_definitions.id"), nullable=False, index=True
    )
    workflow_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_versions.id"), nullable=False
    )
    schedule_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_schedules.id", ondelete="SET NULL"), index=True
    )
    trigger: Mapped[AutomationTrigger] = mapped_column(enum_type(AutomationTrigger), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[AutomationStatus] = mapped_column(
        enum_type(AutomationStatus), nullable=False, default=AutomationStatus.PENDING, index=True
    )
    owner_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AutomationStepExecution(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "automation_step_executions"
    __table_args__ = (
        UniqueConstraint("execution_id", "step_key", name="uq_automation_step_executions_key"),
        UniqueConstraint("execution_id", "ordinal", name="uq_automation_step_executions_ordinal"),
    )

    execution_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("automation_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_key: Mapped[str] = mapped_column(String(100), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    step_type: Mapped[WorkflowStepType] = mapped_column(enum_type(WorkflowStepType), nullable=False)
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[AutomationStepStatus] = mapped_column(
        enum_type(AutomationStepStatus),
        nullable=False,
        default=AutomationStepStatus.PENDING,
        index=True,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), index=True
    )
    output_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    review_decision: Mapped[ReviewDecision | None] = mapped_column(enum_type(ReviewDecision))
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    review_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class ActionRequest(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "action_requests"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_action_requests_idempotency"
        ),
        CheckConstraint("timeout_seconds > 0", name="positive_action_timeout"),
        Index("ix_action_requests_claim", "status", "lease_expires_at", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_type: Mapped[ActionType] = mapped_column(enum_type(ActionType), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    environment: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target: Mapped[dict] = mapped_column(JSON, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    rollback_parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[ActionStatus] = mapped_column(
        enum_type(ActionStatus), nullable=False, default=ActionStatus.DRAFT, index=True
    )
    owner_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    preflight: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class ActionPlan(Base, IdMixin, OrganizationMixin):
    __tablename__ = "action_plans"
    __table_args__ = (UniqueConstraint("action_request_id", name="uq_action_plans_request"),)

    action_request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("action_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionApproval(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "action_approvals"
    __table_args__ = (
        UniqueConstraint(
            "action_request_id",
            "purpose",
            "revision",
            name="uq_action_approvals_revision",
        ),
        CheckConstraint("revision > 0", name="positive_action_approval_revision"),
        Index("ix_action_approvals_pending", "status", "expires_at", "created_at"),
    )

    action_request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("action_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[ActionApprovalPurpose] = mapped_column(
        enum_type(ActionApprovalPurpose), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    approver_constraints: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionAttempt(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "action_attempts"
    __table_args__ = (
        UniqueConstraint(
            "action_request_id", "purpose", "ordinal", name="uq_action_attempts_ordinal"
        ),
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_action_attempts_idempotency"
        ),
        CheckConstraint("ordinal > 0", name="positive_action_attempt_ordinal"),
    )

    action_request_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("action_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[ActionApprovalPurpose] = mapped_column(
        enum_type(ActionApprovalPurpose), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ActionAttemptStatus] = mapped_column(
        enum_type(ActionAttemptStatus),
        nullable=False,
        default=ActionAttemptStatus.PENDING,
        index=True,
    )
    capability_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("capability_versions.id"), nullable=False
    )
    connector_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("connectors.id"), nullable=False)
    approval_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("action_approvals.id"), nullable=False
    )
    policy_decision_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("policy_decisions.id"))
    idempotency_key: Mapped[str] = mapped_column(String(360), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class NotificationDelivery(Base, IdMixin, OrganizationMixin):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key"),
        Index(
            "ix_notification_deliveries_inbox",
            "organization_id",
            "recipient_id",
            "status",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    execution_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("automation_executions.id", ondelete="CASCADE"), index=True
    )
    action_request_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("action_requests.id", ondelete="CASCADE"), index=True
    )
    step_execution_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("automation_step_executions.id", ondelete="SET NULL")
    )
    recipient_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[NotificationStatus] = mapped_column(
        enum_type(NotificationStatus),
        nullable=False,
        default=NotificationStatus.DELIVERED,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
