from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.base import Base
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
    Organization,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    CapabilityTransport,
    CatalogRelationType,
    CatalogResourceKind,
    CatalogResourceState,
    CatalogVerificationLevel,
    Classification,
    CodeSymbolKind,
    ConnectorStatus,
    EvidenceType,
    RegistryStatus,
    RiskLevel,
    SideEffect,
    SystemRole,
)
from obsion.registry.resource_catalog import ResourceCatalogService
from obsion.security.auth import get_principal
from obsion.security.identity import Principal
from obsion.security.roles import SYSTEM_ROLE_DEFINITIONS


@pytest.fixture
async def catalog_database(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'h03-catalog.db'}",
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield database
    finally:
        await database.dispose()


def _principal(
    organization_id: UUID,
    *,
    principal_id: UUID | None = None,
    permissions: set[str] | None = None,
) -> Principal:
    return Principal(
        id=principal_id or uuid4(),
        organization_id=organization_id,
        external_id=f"catalog-{uuid4()}",
        display_name="Catalog tester",
        roles=frozenset({"engineer"}),
        permissions=frozenset(permissions or {"catalog.discover"}),
    )


def _resource(
    organization_id: UUID,
    *,
    kind: CatalogResourceKind,
    key: str,
    name: str,
    source_ref: str,
    permission: str = "catalog.discover",
    access_policy: dict | None = None,
    version: str | None = "v1",
) -> CatalogResource:
    now = utc_now()
    return CatalogResource(
        organization_id=organization_id,
        kind=kind,
        canonical_key=key,
        display_name=name,
        description=f"Authorized description for {name}",
        owner=None,
        version=version,
        state=CatalogResourceState.ACTIVE,
        required_permission=permission,
        classification=Classification.INTERNAL,
        access_policy=access_policy or {},
        search_terms=[],
        source_type="TEST_REGISTRY",
        source_ref=source_ref,
        source_version=version,
        verification_level=CatalogVerificationLevel.DECLARED,
        observed_at=now,
        valid_from=now,
    )


async def _seed_capability(
    session,
    organization_id: UUID,
    *,
    name: str,
    permission: str,
    valid_until: datetime,
    connector_status: ConnectorStatus = ConnectorStatus.ACTIVE,
    binding_enabled: bool = True,
    create_binding: bool = True,
) -> CapabilityVersion:
    now = utc_now()
    definition = CapabilityDefinition(
        organization_id=organization_id,
        name=name,
        display_name=name,
        description=f"Discover {name}",
        status=RegistryStatus.ACTIVE,
    )
    connector = Connector(
        organization_id=organization_id,
        name=f"{name}-service-account",
        connector_type="http-json",
        status=connector_status,
        environment="production",
        endpoint="https://credential-bearing.internal.invalid",
        configuration={"access_token": "must-never-leak"},
        credential_ref="secret://must-never-leak",
        declared_grants=[permission],
        allowed_egress=["credential-bearing.internal.invalid:443"],
        last_health={
            "observed_at": now.isoformat(),
            "valid_until": valid_until.isoformat(),
            "private_diagnostic": "must-never-leak",
        },
    )
    session.add_all([definition, connector])
    await session.flush()
    version = CapabilityVersion(
        organization_id=organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.HTTP,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action=permission,
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"},
        evidence_mapping={"type": EvidenceType.TOOL.value},
        timeout_seconds=12,
        data_classification=Classification.INTERNAL,
        checksum_sha256="a" * 64,
        created_at=now,
    )
    session.add(version)
    await session.flush()
    if create_binding:
        session.add(
            CapabilityBinding(
                organization_id=organization_id,
                capability_version_id=version.id,
                connector_id=connector.id,
                environment="production",
                resource_selector={},
                enabled=binding_enabled,
            )
        )
    await session.flush()
    return version


@pytest.mark.asyncio
async def test_h03_a01_tenants_only_discover_their_own_resources_capabilities_and_edges(
    catalog_database: Database,
) -> None:
    org_a, org_b = uuid4(), uuid4()
    service_a = _resource(
        org_a,
        kind=CatalogResourceKind.SERVICE,
        key="service:checkout-a",
        name="Checkout Search",
        source_ref="service-a",
    )
    deployment_a = _resource(
        org_a,
        kind=CatalogResourceKind.DEPLOYMENT,
        key="deployment:checkout-a:sha-a",
        name="Checkout Search deployment",
        source_ref="deployment-a",
        version="sha-a",
    )
    service_b = _resource(
        org_b,
        kind=CatalogResourceKind.SERVICE,
        key="service:checkout-b",
        name="Checkout Search",
        source_ref="service-b",
    )
    deployment_b = _resource(
        org_b,
        kind=CatalogResourceKind.DEPLOYMENT,
        key="deployment:checkout-b:sha-b",
        name="Checkout Search deployment",
        source_ref="deployment-b",
        version="sha-b",
    )
    repository_a = _resource(
        org_a,
        kind=CatalogResourceKind.REPOSITORY,
        key="repository:checkout-a",
        name="Checkout source A",
        source_ref="repository-a",
        version="sha-a",
    )
    repository_b = _resource(
        org_b,
        kind=CatalogResourceKind.REPOSITORY,
        key="repository:checkout-b",
        name="Checkout source B",
        source_ref="repository-b",
        version="sha-b",
    )
    now = utc_now()
    relation_a = CatalogRelation(
        organization_id=org_a,
        relation_type=CatalogRelationType.SERVICE_DEPLOYED_AS,
        source_kind=CatalogResourceKind.SERVICE,
        source_key=service_a.canonical_key,
        source_version=service_a.version,
        target_kind=CatalogResourceKind.DEPLOYMENT,
        target_key=deployment_a.canonical_key,
        target_version=deployment_a.version,
        provenance_source="DEPLOYMENT_LEDGER",
        provenance_ref="receipt-a",
        provenance_version="sha-a",
        evidence_category=EvidenceType.DEPLOYMENT,
        verification_level=CatalogVerificationLevel.RUNTIME_OBSERVED,
        observed_at=now,
        valid_from=now,
        required_permission="catalog.discover",
    )
    relation_b = CatalogRelation(
        organization_id=org_b,
        relation_type=CatalogRelationType.SERVICE_DEPLOYED_AS,
        source_kind=CatalogResourceKind.SERVICE,
        source_key=service_b.canonical_key,
        source_version=service_b.version,
        target_kind=CatalogResourceKind.DEPLOYMENT,
        target_key=deployment_b.canonical_key,
        target_version=deployment_b.version,
        provenance_source="DEPLOYMENT_LEDGER",
        provenance_ref="receipt-b",
        provenance_version="sha-b",
        evidence_category=EvidenceType.DEPLOYMENT,
        verification_level=CatalogVerificationLevel.RUNTIME_OBSERVED,
        observed_at=now,
        valid_from=now,
        required_permission="catalog.discover",
    )
    source_relation_a = CatalogRelation(
        organization_id=org_a,
        relation_type=CatalogRelationType.DEPLOYMENT_BUILT_FROM,
        source_kind=CatalogResourceKind.DEPLOYMENT,
        source_key=deployment_a.canonical_key,
        source_version="sha-a",
        target_kind=CatalogResourceKind.REPOSITORY,
        target_key=repository_a.canonical_key,
        target_version="sha-a",
        provenance_source="DEPLOYMENT_LEDGER",
        provenance_ref="source-receipt-a",
        provenance_version="sha-a",
        evidence_category=EvidenceType.GIT,
        verification_level=CatalogVerificationLevel.RUNTIME_OBSERVED,
        observed_at=now,
        valid_from=now,
        required_permission="catalog.discover",
    )
    source_relation_b = CatalogRelation(
        organization_id=org_b,
        relation_type=CatalogRelationType.DEPLOYMENT_BUILT_FROM,
        source_kind=CatalogResourceKind.DEPLOYMENT,
        source_key=deployment_b.canonical_key,
        source_version="sha-b",
        target_kind=CatalogResourceKind.REPOSITORY,
        target_key=repository_b.canonical_key,
        target_version="sha-b",
        provenance_source="DEPLOYMENT_LEDGER",
        provenance_ref="source-receipt-b",
        provenance_version="sha-b",
        evidence_category=EvidenceType.GIT,
        verification_level=CatalogVerificationLevel.RUNTIME_OBSERVED,
        observed_at=now,
        valid_from=now,
        required_permission="catalog.discover",
    )
    async with catalog_database.sessions() as session, session.begin():
        session.add_all(
            [
                Organization(id=org_a, slug=f"tenant-a-{org_a}", name="Tenant A"),
                Organization(id=org_b, slug=f"tenant-b-{org_b}", name="Tenant B"),
                service_a,
                deployment_a,
                service_b,
                deployment_b,
                repository_a,
                repository_b,
                relation_a,
                relation_b,
                source_relation_a,
                source_relation_b,
            ]
        )
        version_a = await _seed_capability(
            session,
            org_a,
            name="checkout.search",
            permission="catalog.discover",
            valid_until=now + timedelta(hours=1),
        )
        version_b = await _seed_capability(
            session,
            org_b,
            name="checkout.search",
            permission="catalog.discover",
            valid_until=now + timedelta(hours=1),
        )

    service = ResourceCatalogService()
    async with catalog_database.sessions() as session, session.begin():
        result_a = await service.discover(session, _principal(org_a), query="Checkout Search")
    async with catalog_database.sessions() as session, session.begin():
        result_b = await service.discover(session, _principal(org_b), query="Checkout Search")

    assert {item.canonical_key for item in result_a.resources} == {
        service_a.canonical_key,
        deployment_a.canonical_key,
        repository_a.canonical_key,
    }
    assert {item.canonical_key for item in result_b.resources} == {
        service_b.canonical_key,
        deployment_b.canonical_key,
        repository_b.canonical_key,
    }
    assert {item.version_id for item in result_a.capabilities} == {version_a.id}
    assert {item.version_id for item in result_b.capabilities} == {version_b.id}
    assert {item.provenance.source_ref for item in result_a.relations} == {
        "receipt-a",
        "source-receipt-a",
    }
    assert {item.provenance.source_ref for item in result_b.relations} == {
        "receipt-b",
        "source-receipt-b",
    }


@pytest.mark.asyncio
async def test_h03_a02_connector_service_account_never_grants_individual_resource_access(
    catalog_database: Database,
) -> None:
    organization_id = uuid4()
    allowed_user = uuid4()
    denied_user = uuid4()
    now = utc_now()
    payroll = _resource(
        organization_id,
        kind=CatalogResourceKind.DATA_SOURCE,
        key="data-source:payroll",
        name="Payroll ledger",
        source_ref="payroll-source",
        permission="payroll.read",
        access_policy={"users": [str(allowed_user)]},
    )
    async with catalog_database.sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"service-account-{organization_id}",
                name="Service account tenant",
            )
        )
        session.add(payroll)
        await _seed_capability(
            session,
            organization_id,
            name="unrelated.connector.probe",
            permission="payroll.read",
            valid_until=now + timedelta(hours=1),
        )

    permissions = {"catalog.discover", "payroll.read"}
    service = ResourceCatalogService()
    async with catalog_database.sessions() as session, session.begin():
        denied = await service.discover(
            session,
            _principal(
                organization_id,
                principal_id=denied_user,
                permissions=permissions,
            ),
            query="Payroll ledger",
        )
    async with catalog_database.sessions() as session, session.begin():
        allowed = await service.discover(
            session,
            _principal(
                organization_id,
                principal_id=allowed_user,
                permissions=permissions,
            ),
            query="Payroll ledger",
        )

    assert denied.status.value == "EMPTY"
    assert denied.resources == []
    assert denied.relations == []
    assert allowed.status.value == "RESULTS"
    assert [item.canonical_key for item in allowed.resources] == ["data-source:payroll"]
    denied_payload = denied.model_dump_json()
    assert "Authorized description for Payroll ledger" not in denied_payload
    assert "service-account" not in denied_payload


@pytest.mark.asyncio
async def test_h03_a03_expired_connection_is_stale_not_a_successful_empty_query(
    catalog_database: Database,
) -> None:
    organization_id = uuid4()
    now = utc_now()
    async with catalog_database.sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"expired-{organization_id}",
                name="Expired connector tenant",
            )
        )
        version = await _seed_capability(
            session,
            organization_id,
            name="expired.orders.lookup",
            permission="orders.read",
            valid_until=now - timedelta(minutes=1),
        )

    principal = _principal(
        organization_id,
        permissions={"catalog.discover", "orders.read", "registry.write"},
    )
    service = ResourceCatalogService()
    async with catalog_database.sessions() as session, session.begin():
        stale = await service.discover(session, principal, query="expired.orders.lookup")
    async with catalog_database.sessions() as session, session.begin():
        empty = await service.discover(session, principal, query="does-not-exist")

    assert stale.status.value == "RESULTS"
    assert len(stale.capabilities) == 1
    descriptor = stale.capabilities[0]
    assert descriptor.version_id == version.id
    assert descriptor.availability.value == "STALE"
    assert descriptor.executable_environments == []
    assert descriptor.input_schema == {"type": "object", "additionalProperties": False}
    assert descriptor.output_schema == {"type": "object"}
    assert descriptor.evidence_categories == [EvidenceType.TOOL]
    assert descriptor.permission == "orders.read"
    assert descriptor.cost.declared_timeout_seconds == 12
    assert descriptor.cost.gateway_invocations == 1
    assert descriptor.freshness.valid_until is not None
    assert {item.code for item in stale.repair_actions} == {"REFRESH_CONNECTOR_HEALTH"}
    serialized = stale.model_dump_json()
    for forbidden in (
        "credential-bearing.internal.invalid",
        "must-never-leak",
        "secret://",
        "access_token",
        "private_diagnostic",
    ):
        assert forbidden not in serialized
    assert empty.status.value == "EMPTY"
    assert empty.resources == []
    assert empty.capabilities == []
    assert empty.repair_actions == []


@pytest.mark.asyncio
async def test_capability_catalog_distinguishes_all_connection_states(
    catalog_database: Database,
) -> None:
    organization_id = uuid4()
    now = utc_now()
    async with catalog_database.sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"states-{organization_id}",
                name="Connection state tenant",
            )
        )
        await _seed_capability(
            session,
            organization_id,
            name="state.probe.ready",
            permission="state.read",
            valid_until=now + timedelta(hours=1),
        )
        await _seed_capability(
            session,
            organization_id,
            name="state.probe.stale",
            permission="state.read",
            valid_until=now - timedelta(minutes=1),
        )
        await _seed_capability(
            session,
            organization_id,
            name="state.probe.unavailable",
            permission="state.read",
            valid_until=now + timedelta(hours=1),
            connector_status=ConnectorStatus.ERROR,
        )
        await _seed_capability(
            session,
            organization_id,
            name="state.probe.unconfigured",
            permission="state.read",
            valid_until=now + timedelta(hours=1),
            create_binding=False,
        )

    principal = _principal(
        organization_id,
        permissions={"catalog.discover", "state.read", "registry.write"},
    )
    async with catalog_database.sessions() as session, session.begin():
        result = await ResourceCatalogService().discover(
            session, principal, query="state.probe", limit=10
        )

    assert {item.name: item.availability.value for item in result.capabilities} == {
        "state.probe.ready": "READY",
        "state.probe.stale": "STALE",
        "state.probe.unavailable": "UNAVAILABLE",
        "state.probe.unconfigured": "UNCONFIGURED",
    }
    assert {item.code for item in result.repair_actions} == {
        "REFRESH_CONNECTOR_HEALTH",
        "RESTORE_CONNECTOR",
        "CONFIGURE_CAPABILITY_BINDING",
    }


@pytest.mark.asyncio
async def test_capability_discovery_never_falls_back_to_an_older_weaker_permission(
    catalog_database: Database,
) -> None:
    organization_id = uuid4()
    now = utc_now()
    async with catalog_database.sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"capability-version-{organization_id}",
                name="Capability version tenant",
            )
        )
        version_one = await _seed_capability(
            session,
            organization_id,
            name="governed.versioned.lookup",
            permission="legacy.read",
            valid_until=now + timedelta(hours=1),
        )
        session.add(
            CapabilityVersion(
                organization_id=organization_id,
                capability_id=version_one.capability_id,
                version=2,
                transport=CapabilityTransport.HTTP,
                risk_level=RiskLevel.L1,
                side_effect=SideEffect.NONE,
                permission_action="restricted.read",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                evidence_mapping={"type": EvidenceType.TOOL.value},
                timeout_seconds=12,
                data_classification=Classification.INTERNAL,
                checksum_sha256="d" * 64,
                created_at=now,
            )
        )

    principal = _principal(
        organization_id,
        permissions={"catalog.discover", "legacy.read"},
    )
    async with catalog_database.sessions() as session, session.begin():
        result = await ResourceCatalogService().discover(
            session,
            principal,
            query="governed.versioned.lookup",
        )

    assert result.status.value == "EMPTY"
    assert result.capabilities == []


@pytest.mark.asyncio
async def test_h03_a04_logical_database_never_guesses_a_physical_cluster(
    catalog_database: Database,
) -> None:
    organization_id = uuid4()
    logical = _resource(
        organization_id,
        kind=CatalogResourceKind.LOGICAL_DATA_SOURCE,
        key="logical-data-source:orders",
        name="Orders logical database",
        source_ref="repo/orders/settings.py#L4",
    )
    physical = _resource(
        organization_id,
        kind=CatalogResourceKind.PHYSICAL_DATA_CLUSTER,
        key="physical-data-cluster:production-primary",
        name="Production primary cluster",
        source_ref="deployment-ledger/cluster-1",
    )
    async with catalog_database.sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"logical-{organization_id}",
                name="Logical mapping tenant",
            )
        )
        session.add_all([logical, physical])

    service = ResourceCatalogService()
    async with catalog_database.sessions() as session, session.begin():
        result = await service.discover(
            session,
            _principal(organization_id),
            query="Orders logical database",
        )
    assert [item.kind for item in result.resources] == [CatalogResourceKind.LOGICAL_DATA_SOURCE]
    assert result.relations == []
    assert "production-primary" not in result.model_dump_json()

    invalid = CatalogRelation(
        organization_id=organization_id,
        relation_type=CatalogRelationType.LOGICAL_DATA_SOURCE_MAPS_TO_PHYSICAL_CLUSTER,
        source_kind=logical.kind,
        source_key=logical.canonical_key,
        source_version=logical.version,
        target_kind=physical.kind,
        target_key=physical.canonical_key,
        target_version=physical.version,
        provenance_source="SOURCE_CODE",
        provenance_ref="repo/orders/settings.py#L4",
        provenance_version="sha-static",
        evidence_category=EvidenceType.CODE,
        verification_level=CatalogVerificationLevel.STATIC_INFERENCE,
        observed_at=utc_now(),
        required_permission="catalog.discover",
    )
    async with catalog_database.sessions() as session:
        session.add(invalid)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_existing_code_and_semantic_registries_project_api_table_metric_relations(
    catalog_database: Database,
) -> None:
    organization_id = uuid4()
    now = utc_now()
    async with catalog_database.sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"projection-{organization_id}",
                name="Projection tenant",
            )
        )
        connector = Connector(
            organization_id=organization_id,
            name="warehouse",
            connector_type="postgres-read-only",
            status=ConnectorStatus.ACTIVE,
            environment="production",
            configuration={},
            declared_grants=["data.metadata.read"],
            last_health={"observed_at": now.isoformat()},
        )
        repository = CodeRepository(
            organization_id=organization_id,
            name="orders-service",
            default_branch="main",
            classification=Classification.INTERNAL,
            acl={},
        )
        session.add_all([connector, repository])
        await session.flush()
        snapshot = CodeSnapshot(
            organization_id=organization_id,
            repository_id=repository.id,
            ordinal=1,
            commit_id="9d8f7e6",
            parser_version="h03-test",
            file_count=1,
            symbol_count=2,
            content_checksum_sha256="b" * 64,
            metadata_json={},
            created_at=now,
        )
        session.add(snapshot)
        await session.flush()
        repository.current_snapshot_id = snapshot.id
        source_file = CodeSourceFile(
            organization_id=organization_id,
            snapshot_id=snapshot.id,
            repository_id=repository.id,
            path="orders/api.py",
            language="python",
            content_hash="c" * 64,
            size_bytes=100,
        )
        data_source = DataSource(
            organization_id=organization_id,
            name="orders-warehouse",
            dialect="postgresql",
            connector_id=connector.id,
            environment="production",
            read_only=True,
            classification=Classification.INTERNAL,
            query_policy={},
        )
        session.add_all([source_file, data_source])
        await session.flush()
        api_symbol = CodeSymbol(
            organization_id=organization_id,
            snapshot_id=snapshot.id,
            repository_id=repository.id,
            file_id=source_file.id,
            kind=CodeSymbolKind.API,
            name="get_orders",
            qualified_name="GET /orders",
            start_line=10,
            end_line=24,
            signature="GET /orders",
            attributes={},
        )
        logical_symbol = CodeSymbol(
            organization_id=organization_id,
            snapshot_id=snapshot.id,
            repository_id=repository.id,
            file_id=source_file.id,
            kind=CodeSymbolKind.TABLE,
            name="orders",
            qualified_name="logical.orders",
            start_line=30,
            end_line=30,
            attributes={"sql": True},
        )
        table = DataTable(
            organization_id=organization_id,
            data_source_id=data_source.id,
            schema_name="commerce",
            table_name="orders",
            description="Governed orders business table",
            owner="analytics-team",
            classification=Classification.INTERNAL,
            row_policy={},
        )
        session.add_all([api_symbol, logical_symbol, table])
        await session.flush()
        metric = Metric(
            organization_id=organization_id,
            name="order_count",
            display_name="Order count",
            version=3,
            expression="count(*)",
            filters={},
            time_column="created_at",
            source_table_id=table.id,
            owner="analytics-team",
            synonyms=["orders"],
            validated=True,
        )
        session.add(metric)

    principal = _principal(
        organization_id,
        permissions={
            "catalog.discover",
            "code.read.internal",
            "data.metadata.read",
        },
    )
    async with catalog_database.sessions() as session, session.begin():
        result = await ResourceCatalogService().discover(session, principal, limit=100)

    kinds = {item.kind for item in result.resources}
    assert {
        CatalogResourceKind.REPOSITORY,
        CatalogResourceKind.API,
        CatalogResourceKind.LOGICAL_DATA_SOURCE,
        CatalogResourceKind.DATA_SOURCE,
        CatalogResourceKind.TABLE,
        CatalogResourceKind.METRIC,
    } <= kinds
    relation_types = {item.relation_type for item in result.relations}
    assert CatalogRelationType.REPOSITORY_EXPOSES_API in relation_types
    assert CatalogRelationType.DATA_SOURCE_CONTAINS_TABLE in relation_types
    assert CatalogRelationType.METRIC_DERIVED_FROM_TABLE in relation_types
    assert CatalogRelationType.LOGICAL_DATA_SOURCE_MAPS_TO_PHYSICAL_CLUSTER not in relation_types
    metric_relation = next(
        item
        for item in result.relations
        if item.relation_type == CatalogRelationType.METRIC_DERIVED_FROM_TABLE
    )
    assert metric_relation.source.version == "3"
    assert metric_relation.provenance.source_type == "SEMANTIC_REGISTRY"
    assert metric_relation.provenance.evidence_category == EvidenceType.METRIC


def test_catalog_discovery_returns_generic_unauthorized_without_loading_results(
    client: TestClient,
) -> None:
    denied = Principal(
        id=UUID("00000000-0000-7000-8000-000000000002"),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="catalog-denied",
        display_name="Catalog denied",
        permissions=frozenset(),
    )
    client.app.dependency_overrides[get_principal] = lambda: denied
    try:
        response = client.get("/api/v1/catalog/discovery", params={"q": "hidden-target"})
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "UNAUTHORIZED"
    assert body["resources"] == []
    assert body["capabilities"] == []
    assert body["relations"] == []
    assert body["repair_actions"] == []
    assert "reason" not in response.text.casefold()


def test_catalog_api_vertical_slice_discovers_governed_builtin_capability(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/catalog/discovery",
        params={"q": "knowledge.search", "limit": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "RESULTS"
    descriptor = next(item for item in body["capabilities"] if item["name"] == "knowledge.search")
    assert descriptor["availability"] == "READY"
    assert descriptor["input_schema"]["type"] == "object"
    assert descriptor["output_schema"]["type"] == "object"
    assert descriptor["permission"] == "knowledge.read"
    assert descriptor["evidence_categories"] == ["DOCUMENT"]
    assert descriptor["executable_environments"] == ["development"]
    assert descriptor["policy_decision_id"]
    serialized = response.text.casefold()
    assert "credential_ref" not in serialized
    assert "allowed_egress" not in serialized
    assert "configuration" not in serialized


def test_all_system_roles_receive_discovery_but_only_engineer_can_repair_registry() -> None:
    permissions = {
        definition.name: frozenset(definition.permissions) for definition in SYSTEM_ROLE_DEFINITIONS
    }
    for role in SystemRole:
        assert "*" in permissions[role] or "catalog.discover" in permissions[role]
    assert "registry.write" in permissions[SystemRole.ENGINEER]
    for role in (SystemRole.ANALYST, SystemRole.OPERATOR, SystemRole.SUPPORT, SystemRole.VIEWER):
        assert "registry.write" not in permissions[role]
