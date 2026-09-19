"""Versioned, authority-bound task contract carried by one Harness run."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from obsion.security.redaction import redact_text

TASK_CONTRACT_SCHEMA_VERSION: Literal[1] = 1

SourceScopeMode = Literal[
    "EXACT_ATTACHMENTS",
    "EXACT_DOCUMENTS",
    "EXACT_REPOSITORY",
    "AUTHORIZED_ENTERPRISE",
    "NO_EXTERNAL_SOURCE",
]
TaskDomain = Literal["KNOWLEDGE", "LOCALIZATION", "STATISTICS", "INCIDENT", "CODE"]
RequirementLevel = Literal["HARD", "OPTIONAL"]
AccuracyMode = Literal["EXACT", "EVIDENCE_BOUND", "BEST_EFFORT", "NON_FACTUAL"]
InvalidatedOutput = Literal[
    "PLAN",
    "DATA_QUERY",
    "STATISTICS",
    "EVIDENCE_REVIEW",
    "CLAIMS",
    "ARTIFACTS",
]
ChangedField = Literal[
    "GOAL",
    "SOURCE_SCOPE",
    "OBJECTS",
    "TIME_WINDOW",
    "ACCURACY",
    "DELIVERABLES",
    "CONSTRAINTS",
    "AUTHORITY",
    "BUDGET",
    "SUCCESS_CRITERIA",
    "DIMENSIONS",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TaskSourceReference(ContractModel):
    kind: Literal["ATTACHMENT", "DOCUMENT", "REPOSITORY"]
    identifier: str = Field(min_length=1, max_length=500)
    version: int | None = Field(default=None, ge=1)

    @field_validator("identifier")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("source identifiers cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_reference(self) -> TaskSourceReference:
        if self.kind in {"ATTACHMENT", "DOCUMENT"}:
            try:
                canonical = str(UUID(self.identifier))
            except ValueError as exc:
                raise ValueError(f"{self.kind} references require canonical UUIDs") from exc
            if canonical != self.identifier:
                raise ValueError(f"{self.kind} references require canonical UUIDs")
        if self.kind != "DOCUMENT" and self.version is not None:
            raise ValueError("only document references may pin a version")
        return self


class TaskSourceScope(ContractModel):
    mode: SourceScopeMode
    references: list[TaskSourceReference] = Field(default_factory=list, max_length=50)
    organization_search_allowed: bool = False

    @model_validator(mode="after")
    def validate_scope(self) -> TaskSourceScope:
        identities = [(item.kind, item.identifier, item.version) for item in self.references]
        if len(identities) != len(set(identities)):
            raise ValueError("task source references must be unique")
        exact_kinds = {
            "EXACT_ATTACHMENTS": "ATTACHMENT",
            "EXACT_DOCUMENTS": "DOCUMENT",
            "EXACT_REPOSITORY": "REPOSITORY",
        }
        if expected := exact_kinds.get(self.mode):
            if not self.references or any(item.kind != expected for item in self.references):
                raise ValueError(f"{self.mode} requires only {expected} references")
            if self.organization_search_allowed:
                raise ValueError("an exact source scope cannot authorize organization search")
        elif self.references:
            raise ValueError(f"{self.mode} cannot retain exact source references")
        if self.mode == "AUTHORIZED_ENTERPRISE" and not self.organization_search_allowed:
            raise ValueError("authorized enterprise scope must permit policy-filtered search")
        if self.mode == "NO_EXTERNAL_SOURCE" and self.organization_search_allowed:
            raise ValueError("a source-free task cannot permit organization search")
        return self


class TaskObject(ContractModel):
    kind: Literal["METRIC", "DIMENSION", "SERVICE", "REPOSITORY", "DOCUMENT", "ATTACHMENT"]
    identifier: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, max_length=500)

    @field_validator("identifier", "label")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("task object values cannot be blank")
        return normalized


class TaskTimeWindow(ContractModel):
    start: str = Field(min_length=1, max_length=80)
    end: str = Field(min_length=1, max_length=80)
    timezone: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_window(self) -> TaskTimeWindow:
        try:
            start = datetime.fromisoformat(self.start)
            end = datetime.fromisoformat(self.end)
        except ValueError as exc:
            raise ValueError("task time window must contain ISO-8601 timestamps") from exc
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("task time window must be ordered and timezone-aware")
        if not self.timezone.strip():
            raise ValueError("task time window requires a timezone")
        return self


class TaskAccuracy(ContractModel):
    mode: AccuracyMode
    exact_values_required: bool
    citations_required: bool
    withhold_unverified_claims: bool = True


class TaskDeliverable(ContractModel):
    kind: Literal["ANSWER", "TABLE", "REPORT", "ARTIFACT"]
    format: Literal["AUTO", "TABLE", "BULLETS", "REPORT", "JSON"] = "AUTO"


class TaskAuthority(ContractModel):
    principal_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    organization_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    permission_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_engine_required: Literal[True] = True
    capability_gateway_required: Literal[True] = True
    descriptive_only: Literal[True] = True

    @field_validator("principal_id", "organization_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("authority identifiers must be canonical UUIDs")
        return value


class TaskBudget(ContractModel):
    max_steps: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    max_cost_amount: str = Field(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?$")


class DimensionRequirement(ContractModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    domain: TaskDomain
    level: RequirementLevel
    expected_scope: str = Field(min_length=1, max_length=1000)
    as_of: str | None = Field(default=None, max_length=160)
    precision: str = Field(min_length=1, max_length=160)
    validation_rule: str = Field(min_length=1, max_length=1000)
    acceptable_evidence_kinds: list[str] = Field(min_length=1, max_length=20)

    @field_validator(
        "expected_scope",
        "as_of",
        "precision",
        "validation_rule",
    )
    @classmethod
    def normalize_requirement_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("dimension requirement text cannot be blank")
        return normalized

    @field_validator("acceptable_evidence_kinds")
    @classmethod
    def validate_evidence_kinds(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value]
        if any(not item or len(item) > 80 for item in normalized):
            raise ValueError("evidence kinds must be bounded non-blank identifiers")
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidence kinds must be unique")
        return normalized


class TaskContract(ContractModel):
    schema_version: Literal[1] = TASK_CONTRACT_SCHEMA_VERSION
    revision: int = Field(default=1, ge=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    goal: str = Field(min_length=1, max_length=100_000)
    source_scope: TaskSourceScope
    objects: list[TaskObject] = Field(default_factory=list, max_length=200)
    time_window: TaskTimeWindow | None = None
    accuracy: TaskAccuracy
    deliverables: list[TaskDeliverable] = Field(min_length=1, max_length=10)
    constraints: list[str] = Field(default_factory=list, max_length=24)
    allowed_environments: list[str] = Field(min_length=1, max_length=10)
    authority: TaskAuthority
    budget: TaskBudget
    success_criteria: list[str] = Field(min_length=1, max_length=30)
    dimensions: list[DimensionRequirement] = Field(min_length=1, max_length=50)
    changed_fields: list[ChangedField] = Field(default_factory=list, max_length=12)
    invalidated_outputs: list[InvalidatedOutput] = Field(default_factory=list, max_length=6)

    @field_validator("goal")
    @classmethod
    def normalize_goal(cls, value: str) -> str:
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("task contract goal cannot be blank")
        return normalized

    @field_validator("constraints", "allowed_environments", "success_criteria")
    @classmethod
    def normalize_lists(cls, value: list[str]) -> list[str]:
        normalized = [redact_text(unicodedata.normalize("NFKC", item).strip()) for item in value]
        if any(not item or len(item) > 1000 for item in normalized):
            raise ValueError("task contract list values must contain 1 to 1000 characters")
        if len(normalized) != len(set(normalized)):
            raise ValueError("task contract list values must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> TaskContract:
        object_keys = [(item.kind, item.identifier) for item in self.objects]
        if len(object_keys) != len(set(object_keys)):
            raise ValueError("task objects must be unique")
        dimension_ids = [item.id for item in self.dimensions]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("dimension requirements must be unique")
        if self.revision == 1 and self.parent_fingerprint is not None:
            raise ValueError("an initial task contract cannot have a parent")
        if self.revision > 1 and self.parent_fingerprint is None:
            raise ValueError("a revised task contract requires its parent fingerprint")
        if self.invalidated_outputs and self.revision == 1:
            raise ValueError("an initial task contract cannot invalidate prior outputs")
        expected = task_contract_fingerprint(self.model_dump(mode="json", exclude={"fingerprint"}))
        if self.fingerprint != expected:
            raise ValueError("task contract fingerprint does not match its canonical payload")
        return self


def task_contract_fingerprint(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def create_task_contract(payload: dict[str, Any]) -> TaskContract:
    canonical = dict(payload)
    canonical.pop("fingerprint", None)
    canonical["fingerprint"] = task_contract_fingerprint(canonical)
    return TaskContract.model_validate(canonical)


def contract_from_intent(intent: Any) -> TaskContract | None:
    if not isinstance(intent, dict) or "task_contract" not in intent:
        return None
    if intent["task_contract"] is None:
        return None
    if not isinstance(intent["task_contract"], dict):
        raise ValueError("task contract must be an object")
    return TaskContract.model_validate(intent["task_contract"])


def task_contract_summary(contract: TaskContract) -> dict[str, Any]:
    """Return the one safe summary shared by planning, authoring, review, and output."""

    value = contract.model_dump(mode="json")
    authority = value.pop("authority")
    value["authority"] = {
        "binding_sha256": task_contract_fingerprint(authority),
        "policy_engine_required": True,
        "capability_gateway_required": True,
        "descriptive_only": True,
    }
    return value


def task_contract_summary_from_intent(intent: Any) -> dict[str, Any] | None:
    contract = contract_from_intent(intent)
    return task_contract_summary(contract) if contract is not None else None


def source_scope_allows_organization_search(intent: Any) -> bool:
    contract = contract_from_intent(intent)
    # Legacy persisted intents had no contract and retain their previous behavior.
    return True if contract is None else contract.source_scope.organization_search_allowed


def task_contract_invalidates(intent: Any, output: InvalidatedOutput) -> bool:
    contract = contract_from_intent(intent)
    return bool(contract and output in contract.invalidated_outputs)


def semantic_history_invalidated(intent: Any) -> bool:
    return task_contract_invalidates(intent, "DATA_QUERY") or task_contract_invalidates(
        intent, "STATISTICS"
    )
