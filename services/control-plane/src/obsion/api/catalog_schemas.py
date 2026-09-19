from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from obsion.domain.enums import (
    CapabilityAvailability,
    CapabilityTransport,
    CatalogDiscoveryStatus,
    CatalogRelationType,
    CatalogResourceKind,
    CatalogResourceState,
    CatalogVerificationLevel,
    Classification,
    EvidenceType,
    RiskLevel,
    SideEffect,
)


class CatalogAPIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CatalogProvenanceView(CatalogAPIModel):
    source_type: str
    source_ref: str
    source_version: str | None
    verification_level: CatalogVerificationLevel
    observed_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    evidence_category: EvidenceType | None = None


class CatalogResourceView(CatalogAPIModel):
    id: str
    kind: CatalogResourceKind
    canonical_key: str
    display_name: str
    description: str
    owner: str | None
    version: str | None
    state: CatalogResourceState
    classification: Classification
    required_permission: str
    updated_at: datetime
    provenance: CatalogProvenanceView
    policy_decision_id: UUID


class ConnectorDependencyView(CatalogAPIModel):
    connector_type: str
    environment: str
    state: CapabilityAvailability
    observed_at: datetime | None = None
    valid_until: datetime | None = None


class CapabilityFreshnessView(CatalogAPIModel):
    mode: str
    observed_at: datetime | None = None
    valid_until: datetime | None = None


class CapabilityCostView(CatalogAPIModel):
    basis: str
    cost_class: str
    declared_timeout_seconds: int
    gateway_invocations: int


class CatalogCapabilityView(CatalogAPIModel):
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
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    evidence_categories: list[EvidenceType]
    executable_environments: list[str]
    connector_dependencies: list[ConnectorDependencyView]
    availability: CapabilityAvailability
    freshness: CapabilityFreshnessView
    cost: CapabilityCostView
    data_classification: Classification
    policy_decision_id: UUID


class CatalogResourceRefView(CatalogAPIModel):
    kind: CatalogResourceKind
    canonical_key: str
    version: str | None = None


class CatalogRelationView(CatalogAPIModel):
    id: str
    relation_type: CatalogRelationType
    source: CatalogResourceRefView
    target: CatalogResourceRefView
    state: CatalogResourceState
    provenance: CatalogProvenanceView
    policy_decision_id: UUID


class CatalogRepairActionView(CatalogAPIModel):
    code: str
    target_type: str
    target_ref: str
    summary: str


class CatalogDiscoveryView(CatalogAPIModel):
    status: CatalogDiscoveryStatus
    query: str = Field(max_length=200)
    policy_decision_id: UUID
    resources: list[CatalogResourceView] = Field(default_factory=list)
    capabilities: list[CatalogCapabilityView] = Field(default_factory=list)
    relations: list[CatalogRelationView] = Field(default_factory=list)
    repair_actions: list[CatalogRepairActionView] = Field(default_factory=list)
