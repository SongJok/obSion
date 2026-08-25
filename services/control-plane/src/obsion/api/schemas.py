from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from obsion.domain.enums import (
    ApprovalStatus,
    ArtifactKind,
    Classification,
    EvaluationResultStatus,
    EvaluationTarget,
    MemoryScope,
    MemoryStatus,
    RunStatus,
    StepKind,
    StepStatus,
    ThreadStatus,
    Visibility,
)


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(APIModel):
    code: str
    message: str
    correlation_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class CreateWorkspaceRequest(APIModel):
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    classification: Classification = Classification.INTERNAL
    visibility: Visibility = Visibility.PRIVATE


class WorkspaceView(APIModel):
    id: UUID
    name: str
    description: str
    owner_id: UUID
    classification: Classification
    visibility: Visibility
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class SetWorkspaceMemberRequest(APIModel):
    user_id: UUID
    permissions: list[str] = Field(min_length=1, max_length=2)


class WorkspaceMemberView(APIModel):
    workspace_id: UUID
    user_id: UUID
    permissions: list[str]
    created_by: UUID
    created_at: datetime


class CreateThreadRequest(APIModel):
    workspace_id: UUID
    title: str = Field(min_length=1, max_length=300)


class ThreadView(APIModel):
    id: UUID
    workspace_id: UUID
    title: str
    status: ThreadStatus
    created_by: UUID
    parent_thread_id: UUID | None
    forked_from_turn_id: UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ForkThreadRequest(APIModel):
    from_turn_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class CreateTurnRequest(APIModel):
    input: str = Field(min_length=1, max_length=100_000)
    context_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    attachment_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    model_profile: str | None = Field(default=None, max_length=120)


class TurnView(APIModel):
    id: UUID
    thread_id: UUID
    ordinal: int
    created_by: UUID
    input_text: str
    context_refs: list[dict[str, Any]]
    attachment_refs: list[dict[str, Any]]
    created_at: datetime


class RunView(APIModel):
    id: UUID
    turn_id: UUID
    status: RunStatus
    agent_version_id: UUID | None
    model_profile_id: UUID | None
    intent: dict[str, Any]
    plan: dict[str, Any]
    max_steps: int
    timeout_seconds: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_amount: Decimal
    step_count: int
    input_tokens: int
    output_tokens: int
    cost_amount: Decimal
    started_at: datetime | None
    completed_at: datetime | None
    cancellation_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    replay_of_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


class RunStepView(APIModel):
    id: UUID
    run_id: UUID
    ordinal: int
    name: str
    kind: StepKind
    status: StepStatus
    depends_on: list[int]
    capability_version_id: UUID | None
    output_ref: str | None
    retry_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


class TurnCreatedView(APIModel):
    turn: TurnView
    run: RunView


class EventView(APIModel):
    id: UUID
    sequence: int
    name: str
    run_id: UUID | None
    causation_id: UUID | None
    correlation_id: UUID
    schema_version: int
    classification: Classification
    payload: dict[str, Any]
    created_at: datetime


class ArtifactView(APIModel):
    id: UUID
    workspace_id: UUID
    run_id: UUID | None
    kind: ArtifactKind
    title: str
    media_type: str
    inline_content: dict[str, Any] | None
    storage_key: str | None
    classification: Classification
    lineage: dict[str, Any]
    created_at: datetime


class EvidenceView(APIModel):
    id: UUID
    run_id: UUID
    step_id: UUID | None
    evidence_type: str
    source: str
    resource: str
    observed_at: datetime
    ingested_at: datetime
    content: dict[str, Any]
    content_fingerprint: str
    confidence: Decimal
    classification: Classification
    permissions: list[str]
    lineage: dict[str, Any]


class ClaimView(APIModel):
    id: UUID
    run_id: UUID
    ordinal: int
    statement: str
    confidence: Decimal
    verification_status: str
    critic_notes: dict[str, Any]
    evidence_ids: list[UUID] = Field(default_factory=list)


class ApprovalView(APIModel):
    id: UUID
    run_id: UUID
    step_id: UUID | None
    policy_decision_id: UUID
    status: ApprovalStatus
    reason: str
    requested_by: UUID
    approver_constraints: dict[str, Any]
    decided_by: UUID | None
    decision_reason: str | None
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime


class ApprovalDecisionRequest(APIModel):
    reason: str = Field(min_length=3, max_length=4000)


class CapabilityInvokeRequest(APIModel):
    run_id: UUID
    step_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    resource: dict[str, Any] = Field(default_factory=dict)
    environment: str = Field(default="development", min_length=1, max_length=80)
    agent_name: str = Field(default="external-client", min_length=1, max_length=160)
    capability_version: int | None = Field(default=None, ge=1)


class CapabilityInvokeView(APIModel):
    status: str
    policy_decision_id: UUID
    output: dict[str, Any] | None = None
    evidence_id: UUID | None = None
    approval_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    capability_version_id: UUID | None = None
    connector_id: UUID | None = None


class CreateMemoryRequest(APIModel):
    scope: MemoryScope
    owner_ref: str = Field(min_length=1, max_length=300)
    content: dict[str, Any]
    sensitivity: Classification = Classification.INTERNAL
    expires_at: datetime | None = None


class MemoryDecisionRequest(APIModel):
    reason: str = Field(min_length=3, max_length=1000)


class MemoryView(APIModel):
    id: UUID
    scope: MemoryScope
    owner_ref: str
    content: dict[str, Any]
    dedupe_key: str
    sensitivity: Classification
    status: MemoryStatus
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CreateEvaluationDatasetRequest(APIModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    domain: str = Field(min_length=1, max_length=100)


class EvaluationDatasetView(APIModel):
    id: UUID
    name: str
    description: str
    domain: str
    created_at: datetime
    updated_at: datetime


class CreateEvaluationCaseRequest(APIModel):
    external_id: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    evaluator: EvaluationTarget | None = None
    input_payload: dict[str, Any]
    expected: dict[str, Any]
    fixtures: dict[str, Any] = Field(default_factory=dict)


class EvaluationCaseView(APIModel):
    id: UUID
    dataset_id: UUID
    external_id: str
    version: int
    evaluator: EvaluationTarget
    input_payload: dict[str, Any]
    expected: dict[str, Any]
    fixtures: dict[str, Any]
    created_at: datetime


class StartEvaluationRunRequest(APIModel):
    agent_version_id: UUID
    model_profile_id: UUID
    application_revision: str = Field(min_length=1, max_length=160)
    baseline_run_id: UUID | None = None
    minimum_pass_rate: float = Field(default=1.0, ge=0, le=1)
    maximum_regression_rate: float = Field(default=0.0, ge=0, le=1)
    score_thresholds: dict[str, float] = Field(default_factory=dict)
    run_bindings: dict[str, UUID] = Field(default_factory=dict)


class EvaluationRunView(APIModel):
    id: UUID
    dataset_id: UUID
    agent_version_id: UUID
    model_profile_id: UUID
    application_revision: str
    status: str
    requested_by: UUID | None
    baseline_run_id: UUID | None
    dataset_snapshot_sha256: str
    snapshot_sha256: str
    configuration_snapshot: dict[str, Any]
    gate_passed: bool | None
    metrics: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EvaluationCaseResultView(APIModel):
    id: UUID
    evaluation_run_id: UUID
    evaluation_case_id: UUID
    ordinal: int
    external_id: str
    case_version: int
    evaluator: EvaluationTarget
    status: EvaluationResultStatus
    case_snapshot_sha256: str
    checks: dict[str, Any]
    scores: dict[str, Any]
    observed: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    error_code: str | None
    error_message: str | None
    duration_ms: int
    created_at: datetime


class DocumentView(APIModel):
    id: UUID
    source: str
    external_id: str
    title: str
    classification: Classification
    current_version: int
    created_at: datetime
    updated_at: datetime


class DocumentIngestedView(APIModel):
    document: DocumentView
    version_id: UUID
    version: int
    chunk_count: int


class KnowledgeSearchRequest(APIModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=50)


class KnowledgeHitView(APIModel):
    chunk_id: UUID
    document_id: UUID
    version: int
    title: str
    source: str
    heading_path: list[str]
    content: str
    score: float
    classification: Classification


class DataUnderstandRequest(APIModel):
    question: str = Field(min_length=1, max_length=10_000)


class DataQueryRequest(APIModel):
    thread_id: UUID
    question: str = Field(min_length=1, max_length=10_000)
    model_profile: str | None = Field(default=None, max_length=120)


class DataUnderstandingView(APIModel):
    domain: str
    intent: str
    metrics: list[dict[str, Any]]
    dimensions: list[dict[str, Any]]
    time_range: dict[str, str]
    comparison: str | None
    need_root_cause: bool
    risk: str


class LogicalPlanRequest(APIModel):
    metric_id: UUID
    dimension_ids: list[UUID] = Field(default_factory=list, max_length=20)
    time_range: dict[str, str]
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    comparison: str | None = None


class LogicalPlanView(APIModel):
    plan: dict[str, Any]


class CompileSqlRequest(APIModel):
    plan: dict[str, Any]


class SqlValidationView(APIModel):
    valid: bool
    normalized_sql: str
    tables: list[str] | tuple[str, ...]
    columns: list[str] | tuple[str, ...]
    applied_limit: int
    warnings: list[str] | tuple[str, ...]


class CompiledQueryView(APIModel):
    sql: str
    parameters: list[Any]
    parameter_types: list[str]
    metric: dict[str, Any]
    dimensions: list[dict[str, Any]]
    lineage: dict[str, Any]
    validation: SqlValidationView


class ValidateSqlRequest(APIModel):
    sql: str = Field(min_length=1, max_length=100_000)
    data_source_id: UUID


class MetricView(APIModel):
    id: UUID
    name: str
    display_name: str
    version: int
    expression: str
    filters: dict[str, Any]
    time_column: str
    source_table_id: UUID
    owner: str
    synonyms: list[str]
    validated: bool
    created_at: datetime
    updated_at: datetime


class Page(APIModel):
    items: list[Any]
    next_cursor: str | None = None
