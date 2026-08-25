from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from obsion.domain.enums import (
    Classification,
    ConnectorStatus,
    DecisionEffect,
)


class AdminModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateUserRequest(AdminModel):
    external_id: str = Field(min_length=1, max_length=255)
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    department: str | None = Field(default=None, max_length=200)
    attributes: dict[str, Any] = Field(default_factory=dict)


class CreateRoleRequest(AdminModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    permissions: list[str] = Field(default_factory=list, max_length=500)


class RoleBindingRequest(AdminModel):
    role_id: UUID
    scope: dict[str, Any] = Field(default_factory=dict)


class CreateConnectorRequest(AdminModel):
    name: str = Field(min_length=1, max_length=160)
    connector_type: str = Field(min_length=1, max_length=160)
    environment: str = Field(min_length=1, max_length=80)
    endpoint: str | None = Field(default=None, max_length=1024)
    configuration: dict[str, Any] = Field(default_factory=dict)
    credential_ref: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9+.-]*://.+$")
    declared_grants: list[str] = Field(default_factory=list)
    allowed_egress: list[str] = Field(default_factory=list)
    status: ConnectorStatus = ConnectorStatus.DRAFT


class CreateCapabilityBindingRequest(AdminModel):
    connector_id: UUID
    environment: str = Field(min_length=1, max_length=80)
    resource_selector: dict[str, Any] = Field(default_factory=dict)


class CreateModelEndpointRequest(AdminModel):
    name: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=1024)
    model_id: str = Field(min_length=1, max_length=240)
    credential_ref: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9+.-]*://.+$")
    region: str | None = Field(default=None, max_length=120)
    classifications: list[Classification] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


class ModelProfileBindingRequest(AdminModel):
    endpoint_id: UUID
    priority: int = Field(default=100, ge=0, le=10000)


class CreatePolicyRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=100, ge=0, le=10000)
    effect: DecisionEffect
    conditions: dict[str, Any] = Field(default_factory=dict)
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=4000)
    enabled: bool = True


class CreateDataSourceRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    dialect: str = Field(default="postgres", max_length=80)
    connector_id: UUID
    environment: str = Field(min_length=1, max_length=80)
    classification: Classification = Classification.INTERNAL
    query_policy: dict[str, Any] = Field(default_factory=dict)


class CreateTableRequest(AdminModel):
    data_source_id: UUID
    schema_name: str = Field(min_length=1, max_length=200)
    table_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    owner: str = Field(min_length=1, max_length=200)
    classification: Classification = Classification.INTERNAL
    row_policy: dict[str, Any] = Field(default_factory=dict)


class CreateColumnRequest(AdminModel):
    table_id: UUID
    name: str = Field(min_length=1, max_length=200)
    data_type: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    classification: Classification = Classification.INTERNAL
    mask_policy: dict[str, Any] = Field(default_factory=dict)


class CreateMetricRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=240)
    expression: str = Field(min_length=1, max_length=4000)
    filters: dict[str, Any] = Field(default_factory=dict)
    time_column: str = Field(min_length=1, max_length=200)
    source_table_id: UUID
    owner: str = Field(min_length=1, max_length=200)
    synonyms: list[str] = Field(default_factory=list)
    validated: bool = False


class CreateDimensionRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=240)
    expression: str = Field(min_length=1, max_length=4000)
    source_table_id: UUID
    owner: str = Field(min_length=1, max_length=200)
    synonyms: list[str] = Field(default_factory=list)


class CreateTimeDefinitionRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=240)
    expression: str = Field(min_length=1, max_length=4000)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=120)
    grains: list[str] = Field(default_factory=list, max_length=20)
    fiscal_calendar: dict[str, Any] = Field(default_factory=dict)
    owner: str = Field(min_length=1, max_length=200)


class CreateSemanticSynonymRequest(AdminModel):
    term: str = Field(min_length=1, max_length=300)
    locale: str = Field(default="und", min_length=2, max_length=40)
    target_type: str = Field(pattern=r"^(METRIC|DIMENSION|ENTITY|RULE)$")
    target_id: UUID


class CreatePromptVersionRequest(AdminModel):
    name: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    template: str = Field(min_length=1, max_length=200_000)
    variables_schema: dict[str, Any] = Field(default_factory=dict)


class CreateSecretReferenceRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="env", pattern=r"^env$")
    external_ref: str = Field(pattern=r"^env://[A-Z][A-Z0-9_]*$")
    description: str = Field(default="", max_length=4000)
