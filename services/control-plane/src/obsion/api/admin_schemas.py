import re
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from obsion.domain.enums import (
    Classification,
    ConnectorStatus,
    DecisionEffect,
    SystemRole,
)


class AdminModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class CreateUserRequest(AdminModel):
    external_id: str = Field(min_length=1, max_length=255)
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    department_id: UUID | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CreateDepartmentRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    parent_id: UUID | None = None


class CreateRoleRequest(AdminModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    permissions: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("name")
    @classmethod
    def custom_role_name_cannot_shadow_system_role(cls, value: str) -> str:
        normalized = value.strip()
        if normalized in {role.value for role in SystemRole}:
            raise ValueError("system role names are reserved")
        return normalized

    @field_validator("permissions")
    @classmethod
    def custom_role_permissions_are_explicit(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip() for value in values})
        if "*" in normalized:
            raise ValueError("the wildcard permission is reserved for the admin system role")
        if any(
            not re.fullmatch(r"[a-z][a-z0-9]*(?:[._:-][a-z0-9*]+)*", value) for value in normalized
        ):
            raise ValueError("permissions must be stable lowercase action identifiers")
        return normalized


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

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("capabilities")
    @classmethod
    def normalize_model_capabilities(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().casefold() for value in values})
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]*", value) for value in normalized):
            raise ValueError("model capabilities must be stable lowercase identifiers")
        return normalized

    @field_validator("limits")
    @classmethod
    def validate_model_limits(cls, limits: dict[str, Any]) -> dict[str, Any]:
        for field in ("context_window", "max_output_tokens"):
            value = limits.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{field} must be a positive integer")
        if (
            isinstance(limits.get("context_window"), int)
            and isinstance(limits.get("max_output_tokens"), int)
            and limits["max_output_tokens"] > limits["context_window"]
        ):
            raise ValueError("max_output_tokens cannot exceed context_window")
        if "private" in limits and not isinstance(limits["private"], bool):
            raise ValueError("private must be a boolean")
        pricing = limits.get("pricing_per_million")
        if pricing is not None:
            if not isinstance(pricing, dict):
                raise ValueError("pricing_per_million must be an object")
            for operation, value in pricing.items():
                if operation not in {"input", "output", "embedding"}:
                    raise ValueError("pricing_per_million contains an unsupported operation")
                try:
                    amount = Decimal(str(value))
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError("pricing values must be finite non-negative decimals") from exc
                if not amount.is_finite() or amount < 0:
                    raise ValueError("pricing values must be finite non-negative decimals")
        return limits

    @model_validator(mode="after")
    def enabled_endpoint_must_be_routable(self) -> "CreateModelEndpointRequest":
        if self.enabled and (not self.classifications or not self.capabilities):
            raise ValueError("enabled model endpoints require classifications and capabilities")
        return self


class ModelProfileRequirements(AdminModel):
    capabilities: list[str] = Field(default_factory=lambda: ["chat"], max_length=32)
    providers: list[str] = Field(default_factory=list, max_length=32)
    region: str | None = Field(default=None, max_length=120)
    min_context_window: int = Field(default=0, ge=0, le=10_000_000)
    private: bool = False

    @field_validator("capabilities", "providers")
    @classmethod
    def normalize_requirements(cls, values: list[str]) -> list[str]:
        normalized = sorted({value.strip().casefold() for value in values})
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]*", value) for value in normalized):
            raise ValueError("profile requirements must be stable lowercase identifiers")
        return normalized


class ModelRoutingPolicy(AdminModel):
    fallback: bool = True


class CreateModelProfileRequest(AdminModel):
    name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    requirements: ModelProfileRequirements = Field(default_factory=ModelProfileRequirements)
    routing_policy: ModelRoutingPolicy = Field(default_factory=ModelRoutingPolicy)
    enabled: bool = True


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


class CreateSemanticEntityRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=240)
    primary_key_expression: str = Field(min_length=1, max_length=4000)
    source_table_id: UUID
    owner: str = Field(min_length=1, max_length=200)


class CreateSemanticRelationRequest(AdminModel):
    source_entity_id: UUID
    target_entity_id: UUID
    relation_type: str = Field(min_length=1, max_length=80)
    join_expression: str = Field(min_length=1, max_length=4000)
    cardinality: str = Field(min_length=1, max_length=80)


class CreateBusinessRuleRequest(AdminModel):
    name: str = Field(min_length=1, max_length=200)
    expression: dict[str, Any] = Field(min_length=1)
    owner: str = Field(min_length=1, max_length=200)
    enabled: bool = True


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
