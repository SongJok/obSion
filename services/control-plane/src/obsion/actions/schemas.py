import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from obsion.domain.enums import (
    ActionApprovalPurpose,
    ActionAttemptStatus,
    ActionStatus,
    ActionType,
    ApprovalStatus,
)

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PROJECT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,39}$")


class ActionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateActionRequest(ActionModel):
    action_type: ActionType
    title: str = Field(min_length=3, max_length=240)
    description: str = Field(default="", max_length=10_000)
    environment: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    target: dict[str, Any]
    parameters: dict[str, Any]
    rollback_parameters: dict[str, Any] = Field(default_factory=dict)
    owner_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=300, pattern=r"^[A-Za-z0-9_.:-]+$")
    timeout_seconds: int = Field(default=300, ge=30, le=1800)

    @model_validator(mode="after")
    def validate_action_contract(self) -> "CreateActionRequest":
        serialized = json.dumps(
            {
                "target": self.target,
                "parameters": self.parameters,
                "rollback_parameters": self.rollback_parameters,
            },
            separators=(",", ":"),
            default=str,
        )
        if len(serialized.encode()) > 100_000:
            raise ValueError("action payload cannot exceed 100 KB")
        if self.action_type == ActionType.GENERATE_PR:
            repository = self.target.get("repository")
            if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
                raise ValueError("GENERATE_PR target.repository must be owner/repository")
            _required_strings(self.parameters, ("title", "head", "base"), "GENERATE_PR")
        elif self.action_type == ActionType.CREATE_TICKET:
            project_key = self.target.get("project_key")
            if not isinstance(project_key, str) or not _PROJECT_KEY.fullmatch(project_key):
                raise ValueError("CREATE_TICKET target.project_key is invalid")
            _required_strings(self.parameters, ("summary", "description"), "CREATE_TICKET")
        return self


def _required_strings(payload: dict[str, Any], keys: tuple[str, ...], action: str) -> None:
    for key in keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{action} parameters.{key} is required")
        if len(value) > 20_000:
            raise ValueError(f"{action} parameters.{key} is too long")


class PreflightActionRequest(ActionModel):
    reason: str = Field(min_length=10, max_length=4000)
    approval_ttl_minutes: int = Field(default=60, ge=5, le=1440)


class DecideActionApprovalRequest(ActionModel):
    reason: str = Field(min_length=3, max_length=4000)


class RequestRollbackRequest(ActionModel):
    reason: str = Field(min_length=10, max_length=4000)
    approval_ttl_minutes: int = Field(default=60, ge=5, le=1440)


class ActionRequestView(ActionModel):
    id: UUID
    workspace_id: UUID
    action_type: ActionType
    title: str
    description: str
    environment: str
    target: dict[str, Any]
    parameters: dict[str, Any]
    rollback_parameters: dict[str, Any]
    status: ActionStatus
    owner_id: UUID
    requested_by: UUID
    idempotency_key: str
    timeout_seconds: int
    deadline_at: datetime | None
    plan_checksum_sha256: str | None
    preflight: dict[str, Any]
    result: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    cancellation_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ActionPlanView(ActionModel):
    id: UUID
    action_request_id: UUID
    spec: dict[str, Any]
    checksum_sha256: str
    created_by: UUID
    created_at: datetime


class ActionApprovalView(ActionModel):
    id: UUID
    action_request_id: UUID
    purpose: ActionApprovalPurpose
    revision: int
    plan_checksum_sha256: str
    status: ApprovalStatus
    reason: str
    requested_by: UUID
    approver_constraints: dict[str, Any]
    decided_by: UUID | None
    decision_reason: str | None
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ActionAttemptView(ActionModel):
    id: UUID
    action_request_id: UUID
    purpose: ActionApprovalPurpose
    ordinal: int
    status: ActionAttemptStatus
    capability_version_id: UUID
    connector_id: UUID
    approval_id: UUID
    policy_decision_id: UUID | None
    idempotency_key: str
    output: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ActionDetailView(ActionModel):
    action: ActionRequestView
    plan: ActionPlanView | None
    approvals: list[ActionApprovalView]
    attempts: list[ActionAttemptView]
