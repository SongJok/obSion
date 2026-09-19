from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.catalog_schemas import (
    CapabilityCostView,
    CapabilityFreshnessView,
    CatalogCapabilityView,
    CatalogDiscoveryView,
    CatalogProvenanceView,
    CatalogRelationView,
    CatalogRepairActionView,
    CatalogResourceRefView,
    CatalogResourceView,
    ConnectorDependencyView,
)
from obsion.code_intelligence.service import repository_access_clause
from obsion.common.time import utc_now
from obsion.db.models import (
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    CatalogRelation,
    CatalogResource,
    CodeRepository,
    CodeSnapshot,
    CodeSourceFile,
    CodeSymbol,
    Connector,
    DataSource,
    DataTable,
    Metric,
)
from obsion.domain.enums import (
    CapabilityAvailability,
    CatalogDiscoveryStatus,
    CatalogRelationType,
    CatalogResourceKind,
    CatalogResourceState,
    CatalogVerificationLevel,
    Classification,
    CodeSymbolKind,
    ConnectorStatus,
    DecisionEffect,
    EvidenceType,
    RegistryStatus,
    RiskLevel,
)
from obsion.registry.capability_descriptor import CapabilityDescriptor
from obsion.security.identity import Principal
from obsion.security.policy import Decision, PolicyEngine, ResourcePolicyInput

_DISCOVERY_PERMISSION = "catalog.discover"
_DATA_METADATA_PERMISSION = "data.metadata.read"
_ACL_LIST_KEYS = frozenset(
    {
        "users",
        "roles",
        "departments",
        "deny_users",
        "deny_roles",
        "deny_departments",
    }
)
_ACL_KEYS = _ACL_LIST_KEYS | {"organization"}


@dataclass(frozen=True, slots=True)
class _ResourceCandidate:
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
    source_type: str
    source_ref: str
    source_version: str | None
    verification_level: CatalogVerificationLevel
    observed_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    search_terms: tuple[str, ...]
    policy_decision_id: UUID

    @property
    def ref(self) -> tuple[CatalogResourceKind, str]:
        return self.kind, self.canonical_key

    def as_view(self) -> CatalogResourceView:
        return CatalogResourceView(
            id=self.id,
            kind=self.kind,
            canonical_key=self.canonical_key,
            display_name=self.display_name,
            description=self.description,
            owner=self.owner,
            version=self.version,
            state=self.state,
            classification=self.classification,
            required_permission=self.required_permission,
            updated_at=self.updated_at,
            provenance=CatalogProvenanceView(
                source_type=self.source_type,
                source_ref=self.source_ref,
                source_version=self.source_version,
                verification_level=self.verification_level,
                observed_at=self.observed_at,
                valid_from=self.valid_from,
                valid_until=self.valid_until,
            ),
            policy_decision_id=self.policy_decision_id,
        )


@dataclass(frozen=True, slots=True)
class _RelationCandidate:
    id: str
    relation_type: CatalogRelationType
    source_kind: CatalogResourceKind
    source_key: str
    source_version: str | None
    target_kind: CatalogResourceKind
    target_key: str
    target_version: str | None
    source_type: str
    source_ref: str
    source_provenance_version: str | None
    evidence_category: EvidenceType
    verification_level: CatalogVerificationLevel
    observed_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    required_permission: str
    policy_decision_id: UUID

    @property
    def source(self) -> tuple[CatalogResourceKind, str]:
        return self.source_kind, self.source_key

    @property
    def target(self) -> tuple[CatalogResourceKind, str]:
        return self.target_kind, self.target_key

    def as_view(self, now: datetime) -> CatalogRelationView:
        state = (
            CatalogResourceState.STALE
            if self.valid_until is not None and self.valid_until <= now
            else CatalogResourceState.ACTIVE
        )
        return CatalogRelationView(
            id=self.id,
            relation_type=self.relation_type,
            source=CatalogResourceRefView(
                kind=self.source_kind,
                canonical_key=self.source_key,
                version=self.source_version,
            ),
            target=CatalogResourceRefView(
                kind=self.target_kind,
                canonical_key=self.target_key,
                version=self.target_version,
            ),
            state=state,
            provenance=CatalogProvenanceView(
                source_type=self.source_type,
                source_ref=self.source_ref,
                source_version=self.source_provenance_version,
                verification_level=self.verification_level,
                evidence_category=self.evidence_category,
                observed_at=self.observed_at,
                valid_from=self.valid_from,
                valid_until=self.valid_until,
            ),
            policy_decision_id=self.policy_decision_id,
        )


def validate_catalog_access_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate the small ACL vocabulary accepted by catalog-owned resources."""

    if set(policy) - _ACL_KEYS:
        raise ValueError("catalog access policy contains unsupported fields")
    normalized: dict[str, Any] = {}
    for key in _ACL_LIST_KEYS:
        value = policy.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"catalog access policy field {key} must contain strings")
        normalized[key] = sorted(set(value))
    organization = policy.get("organization", False)
    if not isinstance(organization, bool):
        raise ValueError("catalog access policy organization must be a boolean")
    normalized["organization"] = organization
    return normalized


def _acl_allows(principal: Principal, raw_policy: object) -> bool:
    if not isinstance(raw_policy, dict):
        return False
    try:
        policy = validate_catalog_access_policy(raw_policy)
    except ValueError:
        return False
    department_subjects = {
        value
        for value in (
            principal.department,
            str(principal.department_id) if principal.department_id else None,
        )
        if value
    }
    if str(principal.id) in policy["deny_users"]:
        return False
    if principal.roles.intersection(policy["deny_roles"]):
        return False
    if department_subjects.intersection(policy["deny_departments"]):
        return False
    positive = (
        any(policy[key] for key in ("users", "roles", "departments")) or policy["organization"]
    )
    if not positive:
        return True
    return bool(
        str(principal.id) in policy["users"]
        or principal.roles.intersection(policy["roles"])
        or department_subjects.intersection(policy["departments"])
        or policy["organization"]
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _safe_health_time(health: object, *keys: str) -> datetime | None:
    if not isinstance(health, dict):
        return None
    for key in keys:
        value = health.get(key)
        if isinstance(value, datetime):
            return _aware(value)
        if isinstance(value, str):
            try:
                return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                continue
    return None


def _connector_state(
    connector: Connector, *, enabled: bool, now: datetime
) -> tuple[CapabilityAvailability, datetime | None, datetime | None]:
    observed_at = _safe_health_time(
        connector.last_health, "observed_at", "checked_at", "last_checked_at"
    )
    valid_until = _safe_health_time(connector.last_health, "valid_until", "expires_at")
    if not enabled or connector.status == ConnectorStatus.DRAFT:
        state = CapabilityAvailability.UNCONFIGURED
    elif connector.status in {ConnectorStatus.DISABLED, ConnectorStatus.ERROR}:
        state = CapabilityAvailability.UNAVAILABLE
    elif valid_until is not None and valid_until <= now:
        state = CapabilityAvailability.STALE
    else:
        state = CapabilityAvailability.READY
    return state, observed_at, valid_until


def _resource_state(
    state: CatalogResourceState, valid_until: datetime | None, now: datetime
) -> CatalogResourceState:
    if valid_until is not None and _aware(valid_until) <= now:
        return CatalogResourceState.STALE
    return state


def _rank(texts: tuple[str, ...], query: str) -> tuple[int, int, str]:
    normalized = tuple(text.casefold() for text in texts if text)
    if not query:
        return 0, 0, normalized[0] if normalized else ""
    needle = query.casefold()
    if any(text == needle for text in normalized):
        band = 0
    elif any(text.startswith(needle) for text in normalized):
        band = 1
    elif any(needle in text for text in normalized):
        band = 2
    else:
        terms = tuple(part for part in needle.split() if part)
        if terms and all(any(term in text for text in normalized) for term in terms):
            band = 3
        else:
            return 99, 0, normalized[0] if normalized else ""
    shortest = min((len(text) for text in normalized if needle in text), default=10_000)
    return band, shortest, normalized[0] if normalized else ""


class ResourceCatalogService:
    """Authorization-first discovery over existing registries and catalog facts."""

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()

    async def discover(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        query: str = "",
        kinds: frozenset[CatalogResourceKind] | None = None,
        limit: int = 50,
    ) -> CatalogDiscoveryView:
        normalized_query = " ".join(query.split())[:200]
        now = utc_now()
        gate = await self._decision(
            session,
            principal,
            action=_DISCOVERY_PERMISSION,
            resource_type="enterprise_catalog",
            resource={"scope": "authorized-enterprise-catalog"},
            access_allowed=True,
        )
        if gate.effect != DecisionEffect.ALLOW:
            return CatalogDiscoveryView(
                status=CatalogDiscoveryStatus.UNAUTHORIZED,
                query=normalized_query,
                policy_decision_id=gate.id,
            )

        persisted, reserved_refs = await self._persisted_resources(session, principal, now)
        projected, dynamic_relations = await self._project_existing_registries(
            session, principal, now
        )
        resources_by_ref = {item.ref: item for item in persisted}
        for item in projected:
            if item.ref not in reserved_refs:
                resources_by_ref.setdefault(item.ref, item)

        visible_resources = list(resources_by_ref.values())
        if kinds:
            visible_resources = [item for item in visible_resources if item.kind in kinds]
        all_relations = await self._relations(session, principal, dynamic_relations)
        resources = self._select_resources(
            visible_resources, all_relations, normalized_query, limit
        )
        selected_refs = {item.ref for item in resources}
        relations = [
            item
            for item in all_relations
            if item.source in selected_refs and item.target in selected_refs
        ]
        capabilities = await self._capabilities(session, principal, normalized_query, limit, now)
        repair_actions = (
            self._repair_actions(resources, capabilities) if principal.can("registry.write") else []
        )
        status = (
            CatalogDiscoveryStatus.RESULTS
            if resources or capabilities
            else CatalogDiscoveryStatus.EMPTY
        )
        return CatalogDiscoveryView(
            status=status,
            query=normalized_query,
            policy_decision_id=gate.id,
            resources=[item.as_view() for item in resources],
            capabilities=capabilities,
            relations=[item.as_view(now) for item in relations],
            repair_actions=repair_actions,
        )

    async def _decision(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        action: str,
        resource_type: str,
        resource: dict[str, Any],
        access_allowed: bool,
        capability_version_id: UUID | None = None,
    ) -> Decision:
        return await self.policy_engine.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action=action,
                resource=resource,
                context={"operation": "catalog_discovery"},
                risk_level=RiskLevel.L1,
                resource_type=resource_type,
                capability_version_id=capability_version_id,
                resource_access_allowed=access_allowed,
            ),
        )

    async def _persisted_resources(
        self, session: AsyncSession, principal: Principal, now: datetime
    ) -> tuple[list[_ResourceCandidate], set[tuple[CatalogResourceKind, str]]]:
        envelopes = (
            await session.execute(
                select(
                    CatalogResource.id,
                    CatalogResource.kind,
                    CatalogResource.canonical_key,
                    CatalogResource.required_permission,
                    CatalogResource.classification,
                    CatalogResource.access_policy,
                ).where(CatalogResource.organization_id == principal.organization_id)
            )
        ).all()
        reserved = {(row.kind, row.canonical_key) for row in envelopes}
        decisions: dict[UUID, UUID] = {}
        for row in envelopes:
            decision = await self._decision(
                session,
                principal,
                action=row.required_permission,
                resource_type="catalog_resource",
                resource={
                    "id": str(row.id),
                    "kind": row.kind.value,
                    "canonical_key": row.canonical_key,
                    "classification": row.classification.value,
                },
                access_allowed=_acl_allows(principal, row.access_policy),
            )
            if decision.effect == DecisionEffect.ALLOW:
                decisions[row.id] = decision.id
        if not decisions:
            return [], reserved
        models = list(
            await session.scalars(
                select(CatalogResource)
                .where(CatalogResource.id.in_(decisions))
                .order_by(CatalogResource.kind, CatalogResource.canonical_key)
            )
        )
        return [
            _ResourceCandidate(
                id=str(row.id),
                kind=row.kind,
                canonical_key=row.canonical_key,
                display_name=row.display_name,
                description=row.description,
                owner=row.owner,
                version=row.version,
                state=_resource_state(row.state, row.valid_until, now),
                classification=row.classification,
                required_permission=row.required_permission,
                updated_at=_aware(row.updated_at),
                source_type=row.source_type,
                source_ref=row.source_ref,
                source_version=row.source_version,
                verification_level=row.verification_level,
                observed_at=_aware(row.observed_at),
                valid_from=_aware(row.valid_from) if row.valid_from else None,
                valid_until=_aware(row.valid_until) if row.valid_until else None,
                search_terms=tuple(item for item in row.search_terms if isinstance(item, str)),
                policy_decision_id=decisions[row.id],
            )
            for row in models
        ], reserved

    async def _project_existing_registries(
        self, session: AsyncSession, principal: Principal, now: datetime
    ) -> tuple[list[_ResourceCandidate], list[_RelationCandidate]]:
        resources: list[_ResourceCandidate] = []
        relations: list[_RelationCandidate] = []
        code_resources, code_relations = await self._project_code(session, principal)
        resources.extend(code_resources)
        relations.extend(code_relations)
        data_resources, data_relations = await self._project_data(session, principal, now)
        resources.extend(data_resources)
        relations.extend(data_relations)
        return resources, relations

    async def _project_code(
        self, session: AsyncSession, principal: Principal
    ) -> tuple[list[_ResourceCandidate], list[_RelationCandidate]]:
        allowed_repository_ids = set(
            await session.scalars(
                select(CodeRepository.id).where(
                    CodeRepository.organization_id == principal.organization_id,
                    repository_access_clause(principal),
                )
            )
        )
        envelopes = (
            await session.execute(
                select(
                    CodeRepository.id,
                    CodeRepository.classification,
                    CodeRepository.current_snapshot_id,
                ).where(CodeRepository.organization_id == principal.organization_id)
            )
        ).all()
        repository_decisions: dict[UUID, UUID] = {}
        for row in envelopes:
            decision = await self._decision(
                session,
                principal,
                action=_DISCOVERY_PERMISSION,
                resource_type="code_repository",
                resource={
                    "id": str(row.id),
                    "kind": CatalogResourceKind.REPOSITORY.value,
                    "classification": row.classification.value,
                },
                access_allowed=row.id in allowed_repository_ids,
            )
            if decision.effect == DecisionEffect.ALLOW:
                repository_decisions[row.id] = decision.id
        if not repository_decisions:
            return [], []
        repo_rows = (
            await session.execute(
                select(CodeRepository, CodeSnapshot)
                .outerjoin(CodeSnapshot, CodeSnapshot.id == CodeRepository.current_snapshot_id)
                .where(CodeRepository.id.in_(repository_decisions))
                .order_by(CodeRepository.name)
            )
        ).all()
        resources: list[_ResourceCandidate] = []
        relations: list[_RelationCandidate] = []
        repository_versions: dict[UUID, str | None] = {}
        for repository, snapshot in repo_rows:
            version = snapshot.commit_id if snapshot else None
            observed_at = snapshot.created_at if snapshot else repository.updated_at
            repository_versions[repository.id] = version
            resources.append(
                _ResourceCandidate(
                    id=str(repository.id),
                    kind=CatalogResourceKind.REPOSITORY,
                    canonical_key=f"repository:{repository.id}",
                    display_name=repository.name,
                    description="",
                    owner=None,
                    version=version,
                    state=(
                        CatalogResourceState.ACTIVE
                        if snapshot
                        else CatalogResourceState.UNCONFIGURED
                    ),
                    classification=repository.classification,
                    required_permission=_DISCOVERY_PERMISSION,
                    updated_at=_aware(repository.updated_at),
                    source_type="CODE_REGISTRY",
                    source_ref=str(repository.id),
                    source_version=version,
                    verification_level=CatalogVerificationLevel.RUNTIME_OBSERVED,
                    observed_at=_aware(observed_at),
                    valid_from=None,
                    valid_until=None,
                    search_terms=(),
                    policy_decision_id=repository_decisions[repository.id],
                )
            )
        snapshot_ids = [
            row.current_snapshot_id
            for row in (item[0] for item in repo_rows)
            if row.current_snapshot_id is not None
        ]
        if not snapshot_ids:
            return resources, relations
        symbol_envelopes = (
            await session.execute(
                select(CodeSymbol.id, CodeSymbol.repository_id, CodeSymbol.kind).where(
                    CodeSymbol.organization_id == principal.organization_id,
                    CodeSymbol.snapshot_id.in_(snapshot_ids),
                    CodeSymbol.kind.in_([CodeSymbolKind.API, CodeSymbolKind.TABLE]),
                )
            )
        ).all()
        symbol_decisions: dict[UUID, UUID] = {}
        for symbol_envelope in symbol_envelopes:
            decision = await self._decision(
                session,
                principal,
                action=_DISCOVERY_PERMISSION,
                resource_type="code_symbol",
                resource={
                    "id": str(symbol_envelope.id),
                    "repository_id": str(symbol_envelope.repository_id),
                    "kind": symbol_envelope.kind.value,
                },
                access_allowed=symbol_envelope.repository_id in repository_decisions,
            )
            if decision.effect == DecisionEffect.ALLOW:
                symbol_decisions[symbol_envelope.id] = decision.id
        if not symbol_decisions:
            return resources, relations
        symbol_rows = (
            await session.execute(
                select(CodeSymbol, CodeSourceFile, CodeSnapshot)
                .join(CodeSourceFile, CodeSourceFile.id == CodeSymbol.file_id)
                .join(CodeSnapshot, CodeSnapshot.id == CodeSymbol.snapshot_id)
                .where(CodeSymbol.id.in_(symbol_decisions))
                .order_by(CodeSymbol.qualified_name)
            )
        ).all()
        for symbol, source_file, snapshot in symbol_rows:
            kind = (
                CatalogResourceKind.API
                if symbol.kind == CodeSymbolKind.API
                else CatalogResourceKind.LOGICAL_DATA_SOURCE
            )
            prefix = "code-api" if kind == CatalogResourceKind.API else "logical-data-source"
            key = f"{prefix}:{symbol.id}"
            resources.append(
                _ResourceCandidate(
                    id=str(symbol.id),
                    kind=kind,
                    canonical_key=key,
                    display_name=symbol.qualified_name,
                    description=symbol.signature or "",
                    owner=None,
                    version=snapshot.commit_id,
                    state=CatalogResourceState.ACTIVE,
                    classification=next(
                        repo.classification
                        for repo, _ in repo_rows
                        if repo.id == symbol.repository_id
                    ),
                    required_permission=_DISCOVERY_PERMISSION,
                    updated_at=_aware(snapshot.created_at),
                    source_type="CODE_SNAPSHOT",
                    source_ref=(
                        f"{symbol.repository_id}:{source_file.path}"
                        f"#L{symbol.start_line}-L{symbol.end_line}"
                    ),
                    source_version=snapshot.commit_id,
                    verification_level=CatalogVerificationLevel.STATIC_INFERENCE,
                    observed_at=_aware(snapshot.created_at),
                    valid_from=None,
                    valid_until=None,
                    search_terms=(symbol.name, source_file.path),
                    policy_decision_id=symbol_decisions[symbol.id],
                )
            )
            if kind == CatalogResourceKind.API:
                relation_decision = await self._decision(
                    session,
                    principal,
                    action=_DISCOVERY_PERMISSION,
                    resource_type="catalog_relation",
                    resource={
                        "relation_type": CatalogRelationType.REPOSITORY_EXPOSES_API.value,
                        "repository_id": str(symbol.repository_id),
                        "symbol_id": str(symbol.id),
                    },
                    access_allowed=True,
                )
                if relation_decision.effect == DecisionEffect.ALLOW:
                    relations.append(
                        _RelationCandidate(
                            id=f"code-relation:{symbol.id}",
                            relation_type=CatalogRelationType.REPOSITORY_EXPOSES_API,
                            source_kind=CatalogResourceKind.REPOSITORY,
                            source_key=f"repository:{symbol.repository_id}",
                            source_version=repository_versions[symbol.repository_id],
                            target_kind=CatalogResourceKind.API,
                            target_key=key,
                            target_version=snapshot.commit_id,
                            source_type="CODE_SNAPSHOT",
                            source_ref=str(snapshot.id),
                            source_provenance_version=snapshot.commit_id,
                            evidence_category=EvidenceType.CODE,
                            verification_level=CatalogVerificationLevel.STATIC_INFERENCE,
                            observed_at=_aware(snapshot.created_at),
                            valid_from=None,
                            valid_until=None,
                            required_permission=_DISCOVERY_PERMISSION,
                            policy_decision_id=relation_decision.id,
                        )
                    )
        return resources, relations

    async def _project_data(
        self, session: AsyncSession, principal: Principal, now: datetime
    ) -> tuple[list[_ResourceCandidate], list[_RelationCandidate]]:
        catalog_decision = await self._decision(
            session,
            principal,
            action=_DATA_METADATA_PERMISSION,
            resource_type="semantic_catalog",
            resource={"scope": "authorized-semantic-metadata"},
            access_allowed=True,
        )
        if catalog_decision.effect != DecisionEffect.ALLOW:
            return [], []
        source_envelopes = (
            await session.execute(
                select(DataSource.id, DataSource.classification).where(
                    DataSource.organization_id == principal.organization_id
                )
            )
        ).all()
        source_decisions: dict[UUID, UUID] = {}
        for row in source_envelopes:
            decision = await self._decision(
                session,
                principal,
                action=_DATA_METADATA_PERMISSION,
                resource_type="data_source",
                resource={"id": str(row.id), "classification": row.classification.value},
                access_allowed=True,
            )
            if decision.effect == DecisionEffect.ALLOW:
                source_decisions[row.id] = decision.id
        if not source_decisions:
            return [], []
        source_rows = (
            await session.execute(
                select(DataSource, Connector)
                .join(Connector, Connector.id == DataSource.connector_id)
                .where(DataSource.id.in_(source_decisions))
                .order_by(DataSource.name)
            )
        ).all()
        resources: list[_ResourceCandidate] = []
        relations: list[_RelationCandidate] = []
        source_states: dict[UUID, CatalogResourceState] = {}
        source_by_id = {source.id: (source, connector) for source, connector in source_rows}
        for source, connector in source_rows:
            capability_state, observed, valid_until = _connector_state(
                connector, enabled=True, now=now
            )
            state = CatalogResourceState(capability_state.value.replace("READY", "ACTIVE"))
            source_states[source.id] = state
            resources.append(
                _ResourceCandidate(
                    id=str(source.id),
                    kind=CatalogResourceKind.DATA_SOURCE,
                    canonical_key=f"data-source:{source.id}",
                    display_name=source.name,
                    description="",
                    owner=None,
                    version=None,
                    state=state,
                    classification=source.classification,
                    required_permission=_DATA_METADATA_PERMISSION,
                    updated_at=_aware(source.updated_at),
                    source_type="SEMANTIC_REGISTRY",
                    source_ref=str(source.id),
                    source_version=None,
                    verification_level=CatalogVerificationLevel.DECLARED,
                    observed_at=observed or _aware(source.updated_at),
                    valid_from=None,
                    valid_until=valid_until,
                    search_terms=(source.dialect, source.environment),
                    policy_decision_id=source_decisions[source.id],
                )
            )
        table_envelopes = (
            await session.execute(
                select(DataTable.id, DataTable.data_source_id, DataTable.classification).where(
                    DataTable.organization_id == principal.organization_id,
                    DataTable.data_source_id.in_(source_decisions),
                )
            )
        ).all()
        table_decisions: dict[UUID, UUID] = {}
        for table_envelope in table_envelopes:
            decision = await self._decision(
                session,
                principal,
                action=_DATA_METADATA_PERMISSION,
                resource_type="data_table",
                resource={
                    "id": str(table_envelope.id),
                    "data_source_id": str(table_envelope.data_source_id),
                    "classification": table_envelope.classification.value,
                },
                access_allowed=table_envelope.data_source_id in source_decisions,
            )
            if decision.effect == DecisionEffect.ALLOW:
                table_decisions[table_envelope.id] = decision.id
        tables = (
            list(
                await session.scalars(
                    select(DataTable)
                    .where(DataTable.id.in_(table_decisions))
                    .order_by(DataTable.schema_name, DataTable.table_name)
                )
            )
            if table_decisions
            else []
        )
        for table in tables:
            source, _ = source_by_id[table.data_source_id]
            table_key = f"table:{table.id}"
            resources.append(
                _ResourceCandidate(
                    id=str(table.id),
                    kind=CatalogResourceKind.TABLE,
                    canonical_key=table_key,
                    display_name=f"{table.schema_name}.{table.table_name}",
                    description=table.description,
                    owner=table.owner or None,
                    version=None,
                    state=source_states[table.data_source_id],
                    classification=table.classification,
                    required_permission=_DATA_METADATA_PERMISSION,
                    updated_at=_aware(table.updated_at),
                    source_type="SEMANTIC_REGISTRY",
                    source_ref=str(table.id),
                    source_version=None,
                    verification_level=CatalogVerificationLevel.DECLARED,
                    observed_at=_aware(table.updated_at),
                    valid_from=None,
                    valid_until=None,
                    search_terms=(source.name, table.table_name),
                    policy_decision_id=table_decisions[table.id],
                )
            )
            relation_decision = await self._decision(
                session,
                principal,
                action=_DATA_METADATA_PERMISSION,
                resource_type="catalog_relation",
                resource={
                    "relation_type": CatalogRelationType.DATA_SOURCE_CONTAINS_TABLE.value,
                    "data_source_id": str(source.id),
                    "table_id": str(table.id),
                },
                access_allowed=True,
            )
            if relation_decision.effect == DecisionEffect.ALLOW:
                relations.append(
                    _RelationCandidate(
                        id=f"data-source-table:{table.id}",
                        relation_type=CatalogRelationType.DATA_SOURCE_CONTAINS_TABLE,
                        source_kind=CatalogResourceKind.DATA_SOURCE,
                        source_key=f"data-source:{source.id}",
                        source_version=None,
                        target_kind=CatalogResourceKind.TABLE,
                        target_key=table_key,
                        target_version=None,
                        source_type="SEMANTIC_REGISTRY",
                        source_ref=str(table.id),
                        source_provenance_version=None,
                        evidence_category=EvidenceType.DATA,
                        verification_level=CatalogVerificationLevel.DECLARED,
                        observed_at=_aware(table.updated_at),
                        valid_from=None,
                        valid_until=None,
                        required_permission=_DATA_METADATA_PERMISSION,
                        policy_decision_id=relation_decision.id,
                    )
                )
        metric_envelopes = (
            await session.execute(
                select(Metric.id, Metric.source_table_id).where(
                    Metric.organization_id == principal.organization_id,
                    Metric.source_table_id.in_(table_decisions),
                )
            )
        ).all()
        metric_decisions: dict[UUID, UUID] = {}
        for metric_envelope in metric_envelopes:
            decision = await self._decision(
                session,
                principal,
                action=_DATA_METADATA_PERMISSION,
                resource_type="metric",
                resource={
                    "id": str(metric_envelope.id),
                    "source_table_id": str(metric_envelope.source_table_id),
                },
                access_allowed=metric_envelope.source_table_id in table_decisions,
            )
            if decision.effect == DecisionEffect.ALLOW:
                metric_decisions[metric_envelope.id] = decision.id
        metrics = (
            list(
                await session.scalars(
                    select(Metric).where(Metric.id.in_(metric_decisions)).order_by(Metric.name)
                )
            )
            if metric_decisions
            else []
        )
        tables_by_id = {table.id: table for table in tables}
        for metric in metrics:
            table = tables_by_id[metric.source_table_id]
            metric_key = f"metric:{metric.id}"
            resources.append(
                _ResourceCandidate(
                    id=str(metric.id),
                    kind=CatalogResourceKind.METRIC,
                    canonical_key=metric_key,
                    display_name=metric.display_name,
                    description="",
                    owner=metric.owner or None,
                    version=str(metric.version),
                    state=(
                        source_states[table.data_source_id]
                        if metric.validated
                        else CatalogResourceState.UNAVAILABLE
                    ),
                    classification=table.classification,
                    required_permission=_DATA_METADATA_PERMISSION,
                    updated_at=_aware(metric.updated_at),
                    source_type="SEMANTIC_REGISTRY",
                    source_ref=str(metric.id),
                    source_version=str(metric.version),
                    verification_level=CatalogVerificationLevel.DECLARED,
                    observed_at=_aware(metric.updated_at),
                    valid_from=None,
                    valid_until=None,
                    search_terms=(metric.name, *tuple(str(item) for item in metric.synonyms)),
                    policy_decision_id=metric_decisions[metric.id],
                )
            )
            relation_decision = await self._decision(
                session,
                principal,
                action=_DATA_METADATA_PERMISSION,
                resource_type="catalog_relation",
                resource={
                    "relation_type": CatalogRelationType.METRIC_DERIVED_FROM_TABLE.value,
                    "metric_id": str(metric.id),
                    "table_id": str(table.id),
                },
                access_allowed=True,
            )
            if relation_decision.effect == DecisionEffect.ALLOW:
                relations.append(
                    _RelationCandidate(
                        id=f"metric-table:{metric.id}",
                        relation_type=CatalogRelationType.METRIC_DERIVED_FROM_TABLE,
                        source_kind=CatalogResourceKind.METRIC,
                        source_key=metric_key,
                        source_version=str(metric.version),
                        target_kind=CatalogResourceKind.TABLE,
                        target_key=f"table:{table.id}",
                        target_version=None,
                        source_type="SEMANTIC_REGISTRY",
                        source_ref=str(metric.id),
                        source_provenance_version=str(metric.version),
                        evidence_category=EvidenceType.METRIC,
                        verification_level=CatalogVerificationLevel.DECLARED,
                        observed_at=_aware(metric.updated_at),
                        valid_from=None,
                        valid_until=None,
                        required_permission=_DATA_METADATA_PERMISSION,
                        policy_decision_id=relation_decision.id,
                    )
                )
        return resources, relations

    async def _relations(
        self,
        session: AsyncSession,
        principal: Principal,
        dynamic: list[_RelationCandidate],
    ) -> list[_RelationCandidate]:
        rows = list(
            await session.scalars(
                select(CatalogRelation)
                .where(CatalogRelation.organization_id == principal.organization_id)
                .order_by(CatalogRelation.relation_type, CatalogRelation.source_key)
            )
        )
        persisted: list[_RelationCandidate] = []
        for row in rows:
            decision = await self._decision(
                session,
                principal,
                action=row.required_permission,
                resource_type="catalog_relation",
                resource={
                    "id": str(row.id),
                    "relation_type": row.relation_type.value,
                    "source_kind": row.source_kind.value,
                    "source_key": row.source_key,
                    "target_kind": row.target_kind.value,
                    "target_key": row.target_key,
                },
                access_allowed=True,
            )
            if decision.effect != DecisionEffect.ALLOW:
                continue
            persisted.append(
                _RelationCandidate(
                    id=str(row.id),
                    relation_type=row.relation_type,
                    source_kind=row.source_kind,
                    source_key=row.source_key,
                    source_version=row.source_version,
                    target_kind=row.target_kind,
                    target_key=row.target_key,
                    target_version=row.target_version,
                    source_type=row.provenance_source,
                    source_ref=row.provenance_ref,
                    source_provenance_version=row.provenance_version,
                    evidence_category=row.evidence_category,
                    verification_level=row.verification_level,
                    observed_at=_aware(row.observed_at),
                    valid_from=_aware(row.valid_from) if row.valid_from else None,
                    valid_until=_aware(row.valid_until) if row.valid_until else None,
                    required_permission=row.required_permission,
                    policy_decision_id=decision.id,
                )
            )
        return [*persisted, *dynamic]

    def _select_resources(
        self,
        resources: list[_ResourceCandidate],
        relations: list[_RelationCandidate],
        query: str,
        limit: int,
    ) -> list[_ResourceCandidate]:
        ranked = sorted(
            (
                (
                    _rank(
                        (
                            item.display_name,
                            item.canonical_key,
                            item.description,
                            item.owner or "",
                            *item.search_terms,
                        ),
                        query,
                    ),
                    item,
                )
                for item in resources
            ),
            key=lambda pair: (pair[0], pair[1].kind.value, pair[1].canonical_key),
        )
        selected = [item for rank, item in ranked if rank[0] < 99][:limit]
        if not query or not selected or len(selected) >= limit:
            return selected
        by_ref = {item.ref: item for item in resources}
        selected_refs = {item.ref for item in selected}
        for relation in relations:
            adjacent = None
            if relation.source in selected_refs:
                adjacent = relation.target
            elif relation.target in selected_refs:
                adjacent = relation.source
            if adjacent is None or adjacent in selected_refs or adjacent not in by_ref:
                continue
            selected.append(by_ref[adjacent])
            selected_refs.add(adjacent)
            if len(selected) >= limit:
                break
        return selected

    async def _capabilities(
        self,
        session: AsyncSession,
        principal: Principal,
        query: str,
        limit: int,
        now: datetime,
    ) -> list[CatalogCapabilityView]:
        statement = (
            select(
                CapabilityDefinition.id,
                CapabilityDefinition.name,
                CapabilityVersion.id.label("version_id"),
                CapabilityVersion.version,
                CapabilityVersion.permission_action,
                CapabilityVersion.data_classification,
            )
            .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
            .where(
                CapabilityDefinition.organization_id == principal.organization_id,
                CapabilityDefinition.status == RegistryStatus.ACTIVE,
                CapabilityVersion.organization_id == principal.organization_id,
            )
            .order_by(CapabilityDefinition.name, CapabilityVersion.version.desc())
        )
        envelopes = (await session.execute(statement)).all()
        latest = []
        seen: set[UUID] = set()
        for row in envelopes:
            if row.id in seen:
                continue
            seen.add(row.id)
            latest.append(row)
        decisions: dict[UUID, UUID] = {}
        for row in latest:
            decision = await self._decision(
                session,
                principal,
                action=row.permission_action,
                resource_type="capability_descriptor",
                resource={
                    "id": str(row.id),
                    "version_id": str(row.version_id),
                    "name": row.name,
                    "classification": row.data_classification.value,
                },
                access_allowed=True,
                capability_version_id=row.version_id,
            )
            if decision.effect == DecisionEffect.ALLOW:
                decisions[row.version_id] = decision.id
        if not decisions:
            return []
        model_rows = (
            await session.execute(
                select(CapabilityDefinition, CapabilityVersion)
                .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
                .where(CapabilityVersion.id.in_(decisions))
                .order_by(CapabilityDefinition.name)
            )
        ).all()
        bindings = (
            await session.execute(
                select(CapabilityBinding, Connector)
                .join(Connector, Connector.id == CapabilityBinding.connector_id)
                .where(
                    CapabilityBinding.organization_id == principal.organization_id,
                    CapabilityBinding.capability_version_id.in_(decisions),
                    Connector.organization_id == principal.organization_id,
                )
                .order_by(CapabilityBinding.environment, Connector.connector_type)
            )
        ).all()
        bindings_by_version: dict[UUID, list[tuple[CapabilityBinding, Connector]]] = {}
        for binding, connector in bindings:
            bindings_by_version.setdefault(binding.capability_version_id, []).append(
                (binding, connector)
            )
        ranked: list[tuple[tuple[int, int, str], CatalogCapabilityView]] = []
        for definition, version in model_rows:
            descriptor = CapabilityDescriptor.from_models(definition, version)
            dependencies: list[ConnectorDependencyView] = []
            for binding, connector in bindings_by_version.get(version.id, []):
                state, observed_at, valid_until = _connector_state(
                    connector, enabled=binding.enabled, now=now
                )
                dependencies.append(
                    ConnectorDependencyView(
                        connector_type=connector.connector_type,
                        environment=binding.environment,
                        state=state,
                        observed_at=observed_at,
                        valid_until=valid_until,
                    )
                )
            states = {item.state for item in dependencies}
            if CapabilityAvailability.READY in states:
                availability = CapabilityAvailability.READY
            elif CapabilityAvailability.STALE in states:
                availability = CapabilityAvailability.STALE
            elif CapabilityAvailability.UNAVAILABLE in states:
                availability = CapabilityAvailability.UNAVAILABLE
            else:
                availability = CapabilityAvailability.UNCONFIGURED
            executable_environments = sorted(
                {
                    item.environment
                    for item in dependencies
                    if item.state == CapabilityAvailability.READY
                }
            )
            observed_values = [item.observed_at for item in dependencies if item.observed_at]
            validity_values = [item.valid_until for item in dependencies if item.valid_until]
            evidence_categories: list[EvidenceType] = []
            mapping = descriptor.output.get("mapping", {})
            if isinstance(mapping, dict):
                raw_types = mapping.get("types", [mapping.get("type")])
                if isinstance(raw_types, list):
                    for raw_type in raw_types:
                        try:
                            category = EvidenceType(raw_type)
                        except (TypeError, ValueError):
                            continue
                        if category not in evidence_categories:
                            evidence_categories.append(category)
            cost_class = (
                "LOW"
                if version.timeout_seconds <= 10
                else "BOUNDED"
                if version.timeout_seconds <= 60
                else "HIGH"
            )
            view = CatalogCapabilityView(
                id=definition.id,
                version_id=version.id,
                name=descriptor.name,
                display_name=descriptor.display_name,
                description=descriptor.description,
                version=descriptor.version,
                transport=descriptor.transport,
                risk=descriptor.risk,
                side_effect=descriptor.side_effect,
                permission=descriptor.permission,
                input_schema=descriptor.input_schema,
                output_schema=descriptor.output_schema,
                evidence_categories=evidence_categories,
                executable_environments=executable_environments,
                connector_dependencies=dependencies,
                availability=availability,
                freshness=CapabilityFreshnessView(
                    mode="CONNECTOR_HEALTH" if observed_values or validity_values else "ON_DEMAND",
                    observed_at=max(observed_values) if observed_values else None,
                    valid_until=max(validity_values) if validity_values else None,
                ),
                cost=CapabilityCostView(
                    basis="DECLARED_TIMEOUT_BOUND",
                    cost_class=cost_class,
                    declared_timeout_seconds=version.timeout_seconds,
                    gateway_invocations=1,
                ),
                data_classification=descriptor.data_classification,
                policy_decision_id=decisions[version.id],
            )
            ranked.append(
                (
                    _rank((view.name, view.display_name, view.description), query),
                    view,
                )
            )
        return [
            view
            for rank, view in sorted(ranked, key=lambda pair: (pair[0], pair[1].name))
            if rank[0] < 99
        ][:limit]

    @staticmethod
    def _repair_actions(
        resources: list[_ResourceCandidate], capabilities: list[CatalogCapabilityView]
    ) -> list[CatalogRepairActionView]:
        actions: list[CatalogRepairActionView] = []
        resource_codes = {
            CatalogResourceState.UNCONFIGURED: (
                "CONFIGURE_RESOURCE_SOURCE",
                "Configure an authorized source for this resource.",
            ),
            CatalogResourceState.STALE: (
                "REFRESH_RESOURCE_PROVENANCE",
                "Refresh and re-verify this resource's provenance.",
            ),
            CatalogResourceState.UNAVAILABLE: (
                "RESTORE_RESOURCE_SOURCE",
                "Restore the governed source before selecting this resource.",
            ),
        }
        capability_codes = {
            CapabilityAvailability.UNCONFIGURED: (
                "CONFIGURE_CAPABILITY_BINDING",
                "Create and enable a governed connector binding.",
            ),
            CapabilityAvailability.STALE: (
                "REFRESH_CONNECTOR_HEALTH",
                "Refresh connector health and validity evidence.",
            ),
            CapabilityAvailability.UNAVAILABLE: (
                "RESTORE_CONNECTOR",
                "Restore the governed connector before execution.",
            ),
        }
        for item in resources:
            repair = resource_codes.get(item.state)
            if repair:
                actions.append(
                    CatalogRepairActionView(
                        code=repair[0],
                        target_type="RESOURCE",
                        target_ref=item.canonical_key,
                        summary=repair[1],
                    )
                )
        for capability in capabilities:
            repair = capability_codes.get(capability.availability)
            if repair:
                actions.append(
                    CatalogRepairActionView(
                        code=repair[0],
                        target_type="CAPABILITY",
                        target_ref=str(capability.version_id),
                        summary=repair[1],
                    )
                )
        return actions
