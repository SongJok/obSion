from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from obsion.domain.enums import (
    WorkspaceDecisionStatus,
    WorkspaceTaskPriority,
    WorkspaceTaskStatus,
)


class CollaborationModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateWorkspaceTaskRequest(CollaborationModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    priority: WorkspaceTaskPriority = WorkspaceTaskPriority.NORMAL
    assignee_id: UUID | None = None
    source_run_id: UUID | None = None
    due_at: datetime | None = None


class UpdateWorkspaceTaskRequest(CollaborationModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    status: WorkspaceTaskStatus | None = None
    priority: WorkspaceTaskPriority | None = None
    assignee_id: UUID | None = None
    due_at: datetime | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateWorkspaceTaskRequest":
        mutable = {
            "title",
            "description",
            "status",
            "priority",
            "assignee_id",
            "due_at",
        }
        if not (self.model_fields_set & mutable):
            raise ValueError("at least one task field must be supplied")
        return self


class WorkspaceTaskView(CollaborationModel):
    id: UUID
    workspace_id: UUID
    title: str
    description: str
    status: WorkspaceTaskStatus
    priority: WorkspaceTaskPriority
    assignee_id: UUID | None
    created_by: UUID
    source_run_id: UUID | None
    due_at: datetime | None
    completed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class DecisionContentRequest(CollaborationModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=20_000)
    rationale: str = Field(min_length=1, max_length=40_000)
    alternatives: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("alternatives")
    @classmethod
    def validate_alternatives(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip():
                raise ValueError("decision alternatives cannot be blank")
            if len(value) > 4_000:
                raise ValueError("decision alternatives cannot exceed 4000 characters")
        return values


class CreateWorkspaceDecisionRequest(DecisionContentRequest):
    source_run_id: UUID | None = None
    supersedes_decision_id: UUID | None = None


class ReviseWorkspaceDecisionRequest(DecisionContentRequest):
    expected_version: int = Field(ge=1)


class DecideWorkspaceDecisionRequest(CollaborationModel):
    expected_version: int = Field(ge=1)


class WorkspaceDecisionVersionView(CollaborationModel):
    id: UUID
    decision_id: UUID
    version: int
    title: str
    summary: str
    rationale: str
    alternatives: list[str]
    created_by: UUID
    checksum_sha256: str
    created_at: datetime


class WorkspaceDecisionView(CollaborationModel):
    id: UUID
    workspace_id: UUID
    status: WorkspaceDecisionStatus
    current_version: int
    created_by: UUID
    decided_by: UUID | None
    source_run_id: UUID | None
    supersedes_decision_id: UUID | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime
    title: str
    summary: str
    rationale: str
    alternatives: list[str]
    checksum_sha256: str
