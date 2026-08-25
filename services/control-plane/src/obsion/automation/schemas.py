import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from obsion.domain.enums import (
    AutomationStatus,
    AutomationStepStatus,
    AutomationTrigger,
    Classification,
    NotificationStatus,
    ReviewDecision,
    ScheduleMisfirePolicy,
    WorkflowConcurrencyPolicy,
    WorkflowStatus,
    WorkflowStepType,
)

_STEP_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PLACEHOLDER = re.compile(
    r"\{\{\s*(input(?:\.[a-zA-Z0-9_-]+)+|execution\.id|workflow\.id|scheduled_for)\s*\}\}"
)
_ANY_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")


class AutomationModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkflowStepSpec(AutomationModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    type: WorkflowStepType
    depends_on: list[str] = Field(default_factory=list, max_length=49)
    prompt: str | None = Field(default=None, min_length=1, max_length=100_000)
    model_profile: str | None = Field(default=None, min_length=1, max_length=120)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=20_000)
    review_instructions: str | None = Field(default=None, min_length=1, max_length=20_000)
    disallow_self_review: bool = False

    @model_validator(mode="after")
    def validate_type_contract(self) -> "WorkflowStepSpec":
        if not _STEP_KEY.fullmatch(self.id):
            raise ValueError(
                "step id must start with a letter and contain lowercase safe characters"
            )
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("step dependencies must be unique")
        if self.id in self.depends_on:
            raise ValueError("a workflow step cannot depend on itself")
        if self.type == WorkflowStepType.ANALYSIS and self.prompt is None:
            raise ValueError("analysis steps require a prompt")
        if self.type == WorkflowStepType.NOTIFICATION and (self.title is None or self.body is None):
            raise ValueError("notification steps require title and body")
        if self.type == WorkflowStepType.HUMAN_REVIEW and self.review_instructions is None:
            raise ValueError("human review steps require review instructions")
        for template in (self.prompt, self.title, self.body):
            if template is None:
                continue
            placeholders = _ANY_PLACEHOLDER.findall(template)
            if any(_PLACEHOLDER.fullmatch(item) is None for item in placeholders):
                raise ValueError("workflow template contains an unsupported placeholder")
        return self


class WorkflowSpec(AutomationModel):
    steps: list[WorkflowStepSpec] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_dag(self) -> "WorkflowSpec":
        by_id = {step.id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise ValueError("workflow step ids must be unique")
        for step in self.steps:
            unknown = sorted(set(step.depends_on).difference(by_id))
            if unknown:
                raise ValueError(f"workflow step {step.id} has unknown dependencies: {unknown}")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("workflow dependency graph must be acyclic")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in by_id:
            visit(step_id)
        return self


class CreateWorkflowRequest(AutomationModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    display_name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    owner_id: UUID | None = None
    concurrency_policy: WorkflowConcurrencyPolicy = WorkflowConcurrencyPolicy.FORBID
    max_concurrency: int = Field(default=1, ge=1, le=64)
    timeout_seconds: int = Field(default=3600, ge=60, le=604_800)
    notify_on_success: bool = False
    notify_on_failure: bool = True
    classification: Classification = Classification.INTERNAL
    spec: WorkflowSpec


class CreateWorkflowVersionRequest(AutomationModel):
    spec: WorkflowSpec


class WorkflowView(AutomationModel):
    id: UUID
    workspace_id: UUID
    name: str
    display_name: str
    description: str
    status: WorkflowStatus
    owner_id: UUID
    active_version: int | None
    concurrency_policy: WorkflowConcurrencyPolicy
    max_concurrency: int
    timeout_seconds: int
    notify_on_success: bool
    notify_on_failure: bool
    classification: Classification
    created_at: datetime
    updated_at: datetime


class WorkflowVersionView(AutomationModel):
    id: UUID
    workflow_id: UUID
    version: int
    spec: dict[str, Any]
    checksum_sha256: str
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


class WorkflowCreatedView(AutomationModel):
    workflow: WorkflowView
    version: WorkflowVersionView


class CreateScheduleRequest(AutomationModel):
    name: str = Field(min_length=1, max_length=200)
    cron_expression: str = Field(min_length=9, max_length=100)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    misfire_policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.FIRE_ONCE
    misfire_grace_seconds: int = Field(default=300, ge=0, le=86_400)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    workflow_version: int | None = Field(default=None, ge=1)
    enabled: bool = True


class UpdateScheduleRequest(AutomationModel):
    enabled: bool


class WorkflowScheduleView(AutomationModel):
    id: UUID
    workspace_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    name: str
    cron_expression: str
    timezone: str
    misfire_policy: ScheduleMisfirePolicy
    misfire_grace_seconds: int
    input_payload: dict[str, Any]
    owner_id: UUID
    enabled: bool
    next_fire_at: datetime
    last_fire_at: datetime | None
    last_error_code: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class TriggerWorkflowRequest(AutomationModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class AutomationStepView(AutomationModel):
    id: UUID
    execution_id: UUID
    step_key: str
    ordinal: int
    name: str
    step_type: WorkflowStepType
    depends_on: list[str]
    status: AutomationStepStatus
    run_id: UUID | None
    output_refs: list[dict[str, Any]]
    review_decision: ReviewDecision | None
    reviewed_by: UUID | None
    review_reason: str | None
    reviewed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None


class AutomationExecutionView(AutomationModel):
    id: UUID
    workspace_id: UUID
    workflow_id: UUID
    workflow_version_id: UUID
    schedule_id: UUID | None
    trigger: AutomationTrigger
    scheduled_for: datetime | None
    idempotency_key: str
    status: AutomationStatus
    owner_id: UUID
    input_payload: dict[str, Any]
    max_duration_seconds: int
    deadline_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancellation_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AutomationExecutionDetailView(AutomationExecutionView):
    steps: list[AutomationStepView]


class ReviewAutomationStepRequest(AutomationModel):
    decision: ReviewDecision
    reason: str = Field(min_length=3, max_length=4000)


class NotificationView(AutomationModel):
    id: UUID
    workspace_id: UUID
    execution_id: UUID | None
    action_request_id: UUID | None
    step_execution_id: UUID | None
    recipient_id: UUID
    title: str
    body: str
    payload: dict[str, Any]
    status: NotificationStatus
    delivered_at: datetime
    read_at: datetime | None
    created_at: datetime
