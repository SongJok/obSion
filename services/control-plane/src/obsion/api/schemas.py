from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import AliasPath, BaseModel, ConfigDict, Field, field_validator

from obsion.contracts.errors import validate_error_code
from obsion.domain.enums import (
    ActorType,
    ApprovalStatus,
    ArtifactKind,
    CapabilityTransport,
    Classification,
    EvaluationResultStatus,
    EvaluationTarget,
    ImDeliveryStatus,
    MemoryScope,
    MemoryStatus,
    RiskLevel,
    RunStatus,
    SideEffect,
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
    details: dict[str, Any]

    @field_validator("code")
    @classmethod
    def validate_registered_error_code(cls, value: str) -> str:
        validate_error_code(value)
        return value


class CreateAuthSessionRequest(APIModel):
    access_token: str = Field(min_length=16, max_length=16_384)


class AuthSessionView(APIModel):
    principal_id: UUID
    organization_id: UUID
    display_name: str
    department: str | None
    roles: list[str]


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
    display_name: str
    email: str
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
    prompt_pins: list[dict[str, Any]] = Field(default_factory=list)
    context_budget: dict[str, Any] = Field(default_factory=dict)
    conversation_compact: dict[str, Any] = Field(default_factory=dict)
    workspace_context: dict[str, Any] = Field(default_factory=dict)
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
    event_id: UUID = Field(validation_alias=AliasPath("id"))
    organization_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    sequence: int
    name: str
    run_id: UUID | None
    run_sequence: int | None
    causation_id: UUID | None
    correlation_id: UUID
    actor_type: ActorType
    actor_id: UUID | None
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
    path: str | None = None
    file_version: int | None = None
    superseded_at: datetime | None = None
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
    capability_version_id: UUID | None = None


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


class CapabilityDescriptorView(APIModel):
    id: UUID
    version_id: UUID
    name: str
    display_name: str
    description: str
    version: int
    transport: CapabilityTransport
    risk: RiskLevel
    side_effect: SideEffect
    permission: str
    timeout_seconds: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    output: dict[str, Any]
    data_classification: Classification


class CreateMemoryRequest(APIModel):
    scope: MemoryScope
    owner_ref: str = Field(min_length=1, max_length=300)
    content: dict[str, Any]
    sensitivity: Classification = Classification.INTERNAL
    expires_at: datetime | None = None


class UpdateMemoryRequest(APIModel):
    content: dict[str, Any]
    sensitivity: Classification | None = None
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
    policy_decision_id: UUID | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RunMemorySnapshotView(APIModel):
    id: UUID
    run_id: UUID
    memory_id: UUID
    principal_id: UUID
    ordinal: int
    scope: MemoryScope
    owner_ref: str
    content: dict[str, Any]
    content_fingerprint: str
    sensitivity: Classification
    policy_decision_id: UUID
    memory_updated_at: datetime
    captured_at: datetime


class RunConversationSnapshotView(APIModel):
    id: UUID
    run_id: UUID
    source_thread_id: UUID
    source_turn_id: UUID
    source_run_id: UUID | None
    source_artifact_id: UUID | None
    source_principal_id: UUID
    ordinal: int
    user_content: str
    assistant_content: str | None
    content_fingerprint: str
    classification: Classification
    captured_at: datetime


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
    prompt_pins: dict[str, int] = Field(default_factory=dict)


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


class FeishuDocumentIngestRequest(APIModel):
    document_id: str = Field(min_length=10, max_length=100)
    obj_type: str = Field(default="auto", pattern="^(auto|docx|wiki)$")
    title: str | None = Field(default=None, max_length=500)
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class FeishuDocumentIngestedView(DocumentIngestedView):
    source: str
    external_id: str
    revision_id: str | None
    obj_type: str


class FeishuWikiSpaceView(APIModel):
    space_id: str
    name: str
    description: str


class FeishuWikiNodeView(APIModel):
    space_id: str
    node_token: str
    obj_token: str
    obj_type: str
    title: str


class FeishuSpaceSyncRequest(APIModel):
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class FeishuSpaceSyncView(APIModel):
    operation: str
    space_id: str
    ingested: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    ingested_count: int
    skipped_count: int
    failed_count: int
    budget: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None


class DingTalkDocumentIngestRequest(APIModel):
    document_id: str = Field(min_length=8, max_length=128)
    title: str | None = Field(default=None, max_length=500)
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class DingTalkDocumentIngestedView(DocumentIngestedView):
    source: str
    external_id: str
    revision_id: str | None
    workspace_id: str | None


class DingTalkWorkspaceView(APIModel):
    workspace_id: str
    name: str
    description: str


class DingTalkWorkspaceNodeView(APIModel):
    workspace_id: str
    node_id: str
    document_id: str
    node_type: str
    title: str


class DingTalkWorkspaceSyncRequest(APIModel):
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class DingTalkWorkspaceSyncView(APIModel):
    operation: str
    workspace_id: str
    ingested: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    ingested_count: int
    skipped_count: int
    failed_count: int
    budget: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None


class WeComDocumentIngestRequest(APIModel):
    document_id: str = Field(min_length=8, max_length=128)
    title: str | None = Field(default=None, max_length=500)
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class WeComDocumentIngestedView(DocumentIngestedView):
    source: str
    external_id: str
    revision_id: str | None
    space_id: str | None


class WeComSpaceView(APIModel):
    space_id: str
    name: str
    description: str


class WeComSpaceNodeView(APIModel):
    space_id: str
    node_id: str
    document_id: str
    node_type: str
    title: str


class WeComSpaceSyncRequest(APIModel):
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class WeComSpaceSyncView(APIModel):
    operation: str
    space_id: str
    ingested: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    ingested_count: int
    skipped_count: int
    failed_count: int
    budget: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None


class ConfluencePageIngestRequest(APIModel):
    page_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    title: str | None = Field(default=None, max_length=500)
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class ConfluencePageIngestedView(DocumentIngestedView):
    source: str
    external_id: str
    revision_id: str | None
    space_id: str | None


class ConfluenceSpaceView(APIModel):
    space_id: str
    key: str
    name: str


class ConfluenceSpacePageView(APIModel):
    space_id: str
    page_id: str
    title: str
    status: str


class ConfluenceSpaceSyncRequest(APIModel):
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=dict)
    inherit_acl: bool = False


class ConfluenceSpaceSyncView(APIModel):
    operation: str
    space_id: str
    ingested: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    ingested_count: int
    skipped_count: int
    failed_count: int
    budget: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None


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
    external_id: str | None = None
    revision_id: str | None = None
    connector_name: str | None = None
    operation: str | None = None


class CodeRepositoryView(APIModel):
    id: UUID
    name: str
    default_branch: str
    classification: Classification
    current_snapshot_id: UUID | None
    created_at: datetime
    updated_at: datetime


class CodeSnapshotView(APIModel):
    id: UUID
    repository_id: UUID
    ordinal: int
    commit_id: str
    parser_version: str
    file_count: int
    symbol_count: int
    content_checksum_sha256: str
    metadata_json: dict[str, Any]
    created_at: datetime


class CodeRepositoryIngestedView(APIModel):
    repository: CodeRepositoryView
    snapshot: CodeSnapshotView


class CodeSymbolQuery(APIModel):
    query: str = Field(min_length=1, max_length=4000)
    repository: str | None = Field(default=None, max_length=240)
    limit: int = Field(default=20, ge=1, le=100)


class CodeSymbolHitView(APIModel):
    repository_id: UUID
    repository: str
    commit_id: str
    snapshot_id: UUID
    symbol_id: UUID
    path: str
    language: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    relations: list[dict[str, Any]] = Field(default_factory=list)


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
    statement_type: str = "SELECT"
    estimated_scan_cost: int = 0


class SqlExplainView(SqlValidationView):
    plan: dict[str, Any]
    audit_id: UUID


class CompiledQueryView(APIModel):
    sql: str
    parameters: list[Any]
    parameter_types: list[str]
    metric: dict[str, Any]
    dimensions: list[dict[str, Any]]
    lineage: dict[str, Any]
    validation: SqlValidationView
    column_masks: dict[str, dict[str, Any]] = Field(default_factory=dict)


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


class CreateImBindingRequest(APIModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    channel: str = Field(
        min_length=1,
        max_length=64,
        description="IM identity namespace. Nicknames are not accepted.",
    )
    sender_id: str = Field(
        min_length=1,
        max_length=255,
        description="Stable vendor or development sender id. Display names cannot authorize.",
    )
    user_id: UUID


class ImBindingView(APIModel):
    id: UUID
    channel: str
    sender_id: str
    user_id: UUID
    active: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class CreateImMessageRequest(APIModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    channel: str = Field(min_length=1, max_length=64)
    sender_id: str = Field(
        min_length=1,
        max_length=255,
        description="Stable IM sender id bound to a User. Display names cannot authorize.",
    )
    conversation_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100_000)
    sender_display: str | None = Field(
        default=None,
        max_length=200,
        description="Optional display label. Ignored for authorization and not stored as a key.",
    )


class ImMessageAcceptedView(APIModel):
    binding_id: UUID
    channel: str
    principal_id: UUID
    run_id: UUID
    sender_id: str
    thread_id: UUID
    turn_id: UUID
    workspace_id: UUID


class ImDeliveryPrepareView(APIModel):
    id: UUID
    run_id: UUID
    channel: str
    conversation_id: str
    text: str
    content_fingerprint: str
    idempotency_key: str
    status: ImDeliveryStatus
    attempt_count: int


class CompleteImDeliveryRequest(APIModel):
    model_config = ConfigDict(extra="forbid")
    vendor_message_id: str = Field(min_length=1, max_length=500)


class FailImDeliveryRequest(APIModel):
    model_config = ConfigDict(extra="forbid")
    failure_code: str = Field(
        pattern="^(vendor_request_failed|delivery_audit_failed)$",
    )


class ImDeliveryView(APIModel):
    id: UUID
    run_id: UUID
    channel: str
    conversation_id: str
    content_fingerprint: str
    status: ImDeliveryStatus
    policy_decision_id: UUID
    requested_by: UUID
    attempt_count: int
    vendor_message_id: str | None
    failure_code: str | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StudioDocumentRequest(APIModel):
    document: str = Field(min_length=1, max_length=200_000)


class StudioPromoteRequest(APIModel):
    kind: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)


class StudioCompareRequest(APIModel):
    kind: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    baseline_version: int = Field(ge=1)
    candidate_version: int = Field(ge=1)


class StudioCompareSideView(APIModel):
    version: int
    checksum_sha256: str
    promoted: bool


class StudioChangeView(APIModel):
    path: str
    baseline: Any
    candidate: Any


class StudioCompareView(APIModel):
    kind: str
    name: str
    baseline: StudioCompareSideView
    candidate: StudioCompareSideView
    identical: bool
    changes: list[StudioChangeView]
    traffic_split: bool
    evaluation: str


class StudioValidateView(APIModel):
    kind: str
    name: str
    checksum_sha256: str
    preview: dict[str, Any]


class StudioVersionView(APIModel):
    kind: str
    name: str
    display_name: str
    description: str
    definition_id: UUID
    version_id: UUID
    version: int
    status: str
    checksum_sha256: str
    promoted: bool
    promoted_at: datetime | None
    spec: dict[str, Any]


class StudioCatalogView(APIModel):
    agents: list[StudioVersionView]
    skills: list[StudioVersionView]


class EvalAgentPinView(APIModel):
    name: str
    version: int
    version_id: UUID
    checksum_sha256: str


class EvalProfilePinView(APIModel):
    id: UUID
    name: str


class EvalCatalogView(APIModel):
    datasets: list[EvaluationDatasetView]
    runs: list[EvaluationRunView]
    agents: list[EvalAgentPinView]
    prompts: list[EvalAgentPinView]
    model_profiles: list[EvalProfilePinView]


class EvalCompareRequest(APIModel):
    baseline_run_id: UUID
    candidate_run_id: UUID


class EvalCompareView(APIModel):
    baseline: EvaluationRunView
    candidate: EvaluationRunView
    gate_passed: bool
    metrics: dict[str, Any]
    agent_changed: bool
    prompt_changed: bool
