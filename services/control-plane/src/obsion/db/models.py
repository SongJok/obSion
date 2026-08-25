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
from obsion.domain.enums import (
    ActionApprovalPurpose,
    ActionAttemptStatus,
    ActionStatus,
    ActionType,
    ActorType,
    ApprovalStatus,
    ArtifactKind,
    AutomationStatus,
    AutomationStepStatus,
    AutomationTrigger,
    CapabilityTransport,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    EvidenceType,
    MemoryScope,
    MemoryStatus,
    NotificationStatus,
    RegistryStatus,
    ReviewDecision,
    RiskLevel,
    RunStatus,
    ScheduleMisfirePolicy,
    SideEffect,
    StepKind,
    StepStatus,
    ThreadStatus,
    VerificationStatus,
    Visibility,
    WorkflowConcurrencyPolicy,
    WorkflowStatus,
    WorkflowStepType,
)


def enum_type(enum: type, length: int = 32) -> Enum:
    return Enum(
        enum,
        native_enum=False,
        length=length,
        values_callable=lambda values: [x.value for x in values],
    )


class Organization(Base, IdMixin, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class User(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organization_id", "external_id"),)

    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Role(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UserRole(Base, OrganizationMixin, TimestampMixin):
    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Workspace(Base, IdMixin, OrganizationMixin, TimestampMixin):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    classification: Mapped[Classification] = mapped_column(
        enum_type(Classification), nullable=False, default=Classification.INTERNAL
    )
    visibility: Mapped[Visibility] = mapped_column(
        enum_type(Visibility), nullable=False, default=Visibility.PRIVATE
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceMember(Base, OrganizationMixin):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    can_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    replay_of_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("max_steps > 0", name="positive_max_steps"),
        CheckConstraint("timeout_seconds > 0", name="positive_timeout"),
        CheckConstraint("max_input_tokens > 0", name="positive_max_input_tokens"),
        CheckConstraint("max_output_tokens > 0", name="positive_max_output_tokens"),
        CheckConstraint("max_cost_amount > 0", name="positive_max_cost_amount"),
    )


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
    error_code: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (UniqueConstraint("run_id", "ordinal"),)


class Event(Base, IdMixin, OrganizationMixin):
    __tablename__ = "events"

    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
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
        Index("ix_events_run_sequence", "run_id", "sequence"),
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
    __tablename__ = "audit_records"

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
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="valid_confidence"),
    )


class Claim(Base, IdMixin, OrganizationMixin):
    __tablename__ = "claims"

    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_type(VerificationStatus), nullable=False, default=VerificationStatus.PENDING
    )
    critic_notes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="valid_confidence"),
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
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
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


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
    last_error_code: Mapped[str | None] = mapped_column(String(100))
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
    error_code: Mapped[str | None] = mapped_column(String(100))
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
    error_code: Mapped[str | None] = mapped_column(String(100))
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
    error_code: Mapped[str | None] = mapped_column(String(100))
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
    error_code: Mapped[str | None] = mapped_column(String(100))
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
