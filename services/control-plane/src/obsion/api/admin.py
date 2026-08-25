import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.admin_schemas import (
    CreateCapabilityBindingRequest,
    CreateColumnRequest,
    CreateConnectorRequest,
    CreateDataSourceRequest,
    CreateDimensionRequest,
    CreateMetricRequest,
    CreateModelEndpointRequest,
    CreatePolicyRequest,
    CreatePromptVersionRequest,
    CreateRoleRequest,
    CreateSecretReferenceRequest,
    CreateSemanticSynonymRequest,
    CreateTableRequest,
    CreateTimeDefinitionRequest,
    CreateUserRequest,
    ModelProfileBindingRequest,
    RoleBindingRequest,
)
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    AuditRecord,
    BusinessRule,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    DataColumn,
    DataSource,
    DataTable,
    Dimension,
    Document,
    Metric,
    ModelCall,
    ModelEndpoint,
    ModelProfile,
    ModelProfileEndpoint,
    Policy,
    PromptDefinition,
    PromptVersion,
    Role,
    SecretReference,
    SemanticEntity,
    SemanticRelation,
    SemanticSynonym,
    SkillDefinition,
    SkillVersion,
    TimeDefinition,
    User,
    UserRole,
)
from obsion.domain.enums import (
    ActorType,
    CapabilityTransport,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.egress import validate_model_endpoint
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text

router = APIRouter(prefix="/admin", tags=["administration"])
_SENSITIVE_KEY = re.compile(r"password|passwd|secret|token|api[_-]?key|access[_-]?key", re.I)


def _require_admin(principal: Principal, permission: str = "admin.read") -> None:
    if not principal.can(permission):
        raise AuthorizationError("admin_access_denied", "Administration access is not permitted")


def _safe_configuration(value: Any, path: str = "configuration") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise ValidationError(
                    "inline_secret_denied",
                    "Store a credential reference instead of a secret value",
                    path=f"{path}.{key}",
                )
            _safe_configuration(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _safe_configuration(item, f"{path}.{index}")


def _safe_endpoint(endpoint: str | None) -> None:
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.username or parsed.password:
            raise ValidationError(
                "inline_secret_denied", "Connector URLs cannot contain embedded credentials"
            )


async def _audit_admin(
    session: AsyncSession,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: object,
) -> None:
    await AuditWriter().write(
        session,
        AuditDraft(
            organization_id=principal.organization_id,
            correlation_id=new_id(),
            actor_type=ActorType.USER,
            actor_id=principal.id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            outcome="SUCCESS",
        ),
    )


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    users = await session.scalars(
        select(User)
        .where(User.organization_id == principal.organization_id)
        .order_by(User.display_name)
    )
    return [
        {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "department": user.department,
            "active": user.active,
        }
        for user in users
    ]


@router.get("/departments")
async def list_departments(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal)
    rows = (
        await session.execute(
            select(User.department, func.count(User.id))
            .where(
                User.organization_id == principal.organization_id,
                User.active.is_(True),
                User.department.is_not(None),
            )
            .group_by(User.department)
            .order_by(User.department)
        )
    ).all()
    return [
        {"name": department, "active_user_count": count} for department, count in rows if department
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "identity.write")
    async with session.begin():
        user = User(
            organization_id=principal.organization_id,
            external_id=request.external_id,
            email=str(request.email),
            display_name=request.display_name,
            department=request.department,
            active=True,
            attributes=request.attributes,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "user_exists", "A user with this external identity already exists"
            ) from exc
        await _audit_admin(session, principal, "identity.user.create", "user", user.id)
    return {"id": str(user.id)}


@router.get("/roles")
async def list_roles(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    roles = await session.scalars(
        select(Role).where(Role.organization_id == principal.organization_id).order_by(Role.name)
    )
    return [
        {"id": str(role.id), "name": role.name, "permissions": role.permissions} for role in roles
    ]


@router.post("/roles", status_code=status.HTTP_201_CREATED)
async def create_role(
    request: CreateRoleRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "identity.write")
    async with session.begin():
        role = Role(
            organization_id=principal.organization_id,
            name=request.name,
            description=request.description,
            permissions=request.permissions,
            system=False,
        )
        session.add(role)
        await session.flush()
        await _audit_admin(session, principal, "identity.role.create", "role", role.id)
    return {"id": str(role.id)}


@router.post("/users/{user_id}/roles", status_code=status.HTTP_204_NO_CONTENT)
async def bind_role(
    user_id: UUID,
    request: RoleBindingRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> None:
    _require_admin(principal, "identity.write")
    async with session.begin():
        user = await session.scalar(
            select(User).where(
                User.id == user_id, User.organization_id == principal.organization_id
            )
        )
        role = await session.scalar(
            select(Role).where(
                Role.id == request.role_id,
                Role.organization_id == principal.organization_id,
            )
        )
        if user is None or role is None:
            raise NotFoundError("User or role", f"{user_id}/{request.role_id}")
        existing = await session.get(UserRole, (user.id, role.id))
        if existing is None:
            session.add(
                UserRole(
                    organization_id=principal.organization_id,
                    user_id=user.id,
                    role_id=role.id,
                    scope=request.scope,
                )
            )
        await _audit_admin(session, principal, "identity.role.bind", "user", user.id)


@router.get("/connectors")
async def list_connectors(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    connectors = await session.scalars(
        select(Connector)
        .where(Connector.organization_id == principal.organization_id)
        .order_by(Connector.name)
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "type": item.connector_type,
            "status": item.status,
            "environment": item.environment,
            "endpoint": item.endpoint,
            "has_credential": bool(item.credential_ref),
            "health": item.last_health,
        }
        for item in connectors
    ]


@router.post("/connectors", status_code=status.HTTP_201_CREATED)
async def create_connector(
    request: CreateConnectorRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "connectors.write")
    _safe_configuration(request.configuration)
    _safe_endpoint(request.endpoint)
    async with session.begin():
        connector = Connector(
            organization_id=principal.organization_id,
            name=request.name,
            connector_type=request.connector_type,
            status=request.status,
            environment=request.environment,
            endpoint=request.endpoint,
            configuration=request.configuration,
            credential_ref=request.credential_ref,
            declared_grants=request.declared_grants,
            allowed_egress=request.allowed_egress,
            last_health={"status": "unknown"},
        )
        session.add(connector)
        await session.flush()
        await _audit_admin(session, principal, "connector.create", "connector", connector.id)
    return {"id": str(connector.id)}


@router.get("/capabilities")
async def list_capabilities(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    rows = (
        await session.execute(
            select(CapabilityDefinition, CapabilityVersion)
            .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
            .where(CapabilityDefinition.organization_id == principal.organization_id)
            .order_by(CapabilityDefinition.name, CapabilityVersion.version.desc())
        )
    ).all()
    return [
        {
            "id": str(definition.id),
            "version_id": str(version.id),
            "name": definition.name,
            "description": definition.description,
            "version": version.version,
            "transport": version.transport,
            "risk": version.risk_level,
            "side_effect": version.side_effect,
            "permission": version.permission_action,
        }
        for definition, version in rows
    ]


@router.post("/capabilities/{capability_id}/bindings", status_code=status.HTTP_201_CREATED)
async def bind_capability(
    capability_id: UUID,
    request: CreateCapabilityBindingRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "capabilities.write")
    async with session.begin():
        row = (
            await session.execute(
                select(CapabilityDefinition, CapabilityVersion)
                .join(
                    CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.capability_id
                )
                .where(
                    CapabilityDefinition.id == capability_id,
                    CapabilityDefinition.organization_id == principal.organization_id,
                )
                .order_by(CapabilityVersion.version.desc())
                .limit(1)
            )
        ).one_or_none()
        connector = await session.scalar(
            select(Connector).where(
                Connector.id == request.connector_id,
                Connector.organization_id == principal.organization_id,
            )
        )
        if row is None or connector is None:
            raise NotFoundError("Capability or connector", capability_id)
        definition, version = row._tuple()
        if version.side_effect != SideEffect.NONE:
            _require_admin(principal, "actions.configure")
            if (
                definition.name
                not in {
                    "action.pr.create",
                    "action.pr.close",
                    "action.ticket.create",
                    "action.ticket.close",
                }
                or version.risk_level != RiskLevel.L3
                or version.side_effect != SideEffect.IDEMPOTENT_WRITE
                or version.transport != CapabilityTransport.HTTP
                or request.environment not in {"development", "staging"}
            ):
                raise ValidationError(
                    "v1_action_binding_boundary",
                    "V1 only binds approved L3 idempotent PR and ticket action capabilities",
                )
            if connector.environment != request.environment:
                raise ValidationError(
                    "connector_environment_mismatch",
                    "The connector and capability binding environments must match",
                )
            if version.permission_action not in connector.declared_grants:
                raise ValidationError(
                    "connector_grant_missing",
                    "The connector must declare the action capability permission",
                )
        binding = CapabilityBinding(
            organization_id=principal.organization_id,
            capability_version_id=version.id,
            connector_id=connector.id,
            environment=request.environment,
            resource_selector=request.resource_selector,
            enabled=True,
        )
        session.add(binding)
        await session.flush()
        await _audit_admin(session, principal, "capability.bind", "capability", capability_id)
    return {"id": str(binding.id)}


@router.get("/models/profiles")
async def list_model_profiles(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    profiles = await session.scalars(
        select(ModelProfile)
        .where(ModelProfile.organization_id == principal.organization_id)
        .order_by(ModelProfile.name)
    )
    return [{"id": str(item.id), "name": item.name, "enabled": item.enabled} for item in profiles]


@router.post("/models/endpoints", status_code=status.HTTP_201_CREATED)
async def create_model_endpoint(
    request: CreateModelEndpointRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    _require_admin(principal, "models.write")
    _safe_endpoint(request.base_url)
    validate_model_endpoint(
        request.base_url,
        settings.model_allowed_hosts,
        allow_insecure_loopback=settings.environment in {Environment.DEVELOPMENT, Environment.TEST},
    )
    _safe_configuration(request.limits, "limits")
    async with session.begin():
        endpoint = ModelEndpoint(
            organization_id=principal.organization_id,
            name=request.name,
            provider=request.provider,
            base_url=request.base_url,
            model_id=request.model_id,
            credential_ref=request.credential_ref,
            region=request.region,
            classifications=[item.value for item in request.classifications],
            capabilities=request.capabilities,
            limits=request.limits,
            enabled=request.enabled,
        )
        session.add(endpoint)
        await session.flush()
        await _audit_admin(
            session, principal, "model.endpoint.create", "model_endpoint", endpoint.id
        )
    return {"id": str(endpoint.id)}


@router.post("/models/profiles/{profile_id}/endpoints", status_code=status.HTTP_201_CREATED)
async def bind_model_endpoint(
    profile_id: UUID,
    request: ModelProfileBindingRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "models.write")
    async with session.begin():
        profile = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.id == profile_id,
                ModelProfile.organization_id == principal.organization_id,
            )
        )
        endpoint = await session.scalar(
            select(ModelEndpoint).where(
                ModelEndpoint.id == request.endpoint_id,
                ModelEndpoint.organization_id == principal.organization_id,
            )
        )
        if profile is None or endpoint is None:
            raise NotFoundError("Model profile or endpoint", profile_id)
        binding = ModelProfileEndpoint(
            profile_id=profile.id,
            endpoint_id=endpoint.id,
            priority=request.priority,
        )
        session.add(binding)
        await _audit_admin(session, principal, "model.profile.bind", "model_profile", profile.id)
    return {"profile_id": str(profile.id), "endpoint_id": str(endpoint.id)}


@router.post("/policies", status_code=status.HTTP_201_CREATED)
async def create_policy(
    request: CreatePolicyRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "policies.write")
    async with session.begin():
        version = (
            await session.scalar(
                select(func.max(Policy.version)).where(
                    Policy.organization_id == principal.organization_id,
                    Policy.name == request.name,
                )
            )
            or 0
        ) + 1
        policy = Policy(
            organization_id=principal.organization_id,
            name=request.name,
            version=version,
            priority=request.priority,
            effect=request.effect,
            enabled=request.enabled,
            conditions=request.conditions,
            obligations=request.obligations,
            reason=request.reason,
            created_by=principal.id,
        )
        session.add(policy)
        await session.flush()
        await _audit_admin(session, principal, "policy.create", "policy", policy.id)
    return {"id": str(policy.id), "version": policy.version}


@router.get("/policies")
async def list_policies(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    policies = await session.scalars(
        select(Policy)
        .where(Policy.organization_id == principal.organization_id)
        .order_by(Policy.name, Policy.version.desc())
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "version": item.version,
            "effect": item.effect,
            "enabled": item.enabled,
            "conditions": item.conditions,
            "obligations": item.obligations,
        }
        for item in policies
    ]


@router.post("/data/sources", status_code=status.HTTP_201_CREATED)
async def create_data_source(
    request: CreateDataSourceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        connector = await session.scalar(
            select(Connector).where(
                Connector.id == request.connector_id,
                Connector.organization_id == principal.organization_id,
            )
        )
        if connector is None:
            raise NotFoundError("Connector", request.connector_id)
        source = DataSource(
            organization_id=principal.organization_id,
            name=request.name,
            dialect=request.dialect,
            connector_id=connector.id,
            environment=request.environment,
            read_only=True,
            classification=request.classification,
            query_policy=request.query_policy,
        )
        session.add(source)
        await session.flush()
        await _audit_admin(session, principal, "data.source.create", "data_source", source.id)
    return {"id": str(source.id), "read_only": True}


@router.get("/data/sources")
async def list_data_sources(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal)
    sources = await session.scalars(
        select(DataSource)
        .where(DataSource.organization_id == principal.organization_id)
        .order_by(DataSource.name)
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "dialect": item.dialect,
            "environment": item.environment,
            "read_only": item.read_only,
            "classification": item.classification,
            "connector_id": str(item.connector_id),
        }
        for item in sources
    ]


@router.get("/data/catalog")
async def data_catalog_summary(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, int]:
    _require_admin(principal)
    catalog_models: dict[str, Any] = {
        "sources": DataSource,
        "tables": DataTable,
        "columns": DataColumn,
        "metrics": Metric,
        "dimensions": Dimension,
        "entities": SemanticEntity,
        "relations": SemanticRelation,
        "rules": BusinessRule,
        "time_definitions": TimeDefinition,
        "synonyms": SemanticSynonym,
    }
    counts: dict[str, int] = {}
    for name, model in catalog_models.items():
        value = await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.organization_id == principal.organization_id)
        )
        counts[name] = int(value or 0)
    return counts


@router.post("/data/tables", status_code=status.HTTP_201_CREATED)
async def create_table(
    request: CreateTableRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        source = await session.scalar(
            select(DataSource).where(
                DataSource.id == request.data_source_id,
                DataSource.organization_id == principal.organization_id,
            )
        )
        if source is None:
            raise NotFoundError("Data source", request.data_source_id)
        table = DataTable(organization_id=principal.organization_id, **request.model_dump())
        session.add(table)
        await session.flush()
        await _audit_admin(session, principal, "data.table.create", "data_table", table.id)
    return {"id": str(table.id)}


@router.post("/data/columns", status_code=status.HTTP_201_CREATED)
async def create_column(
    request: CreateColumnRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        table = await session.scalar(
            select(DataTable).where(
                DataTable.id == request.table_id,
                DataTable.organization_id == principal.organization_id,
            )
        )
        if table is None:
            raise NotFoundError("Data table", request.table_id)
        column = DataColumn(organization_id=principal.organization_id, **request.model_dump())
        session.add(column)
        await session.flush()
    return {"id": str(column.id)}


@router.post("/data/metrics", status_code=status.HTTP_201_CREATED)
async def create_metric(
    request: CreateMetricRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        table = await session.scalar(
            select(DataTable).where(
                DataTable.id == request.source_table_id,
                DataTable.organization_id == principal.organization_id,
            )
        )
        if table is None:
            raise NotFoundError("Data table", request.source_table_id)
        version = (
            await session.scalar(
                select(func.max(Metric.version)).where(
                    Metric.organization_id == principal.organization_id,
                    Metric.name == request.name,
                )
            )
            or 0
        ) + 1
        metric = Metric(
            organization_id=principal.organization_id,
            version=version,
            **request.model_dump(),
        )
        session.add(metric)
        await session.flush()
        await _audit_admin(session, principal, "data.metric.create", "metric", metric.id)
    return {"id": str(metric.id), "version": version}


@router.post("/data/dimensions", status_code=status.HTTP_201_CREATED)
async def create_dimension(
    request: CreateDimensionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        version = (
            await session.scalar(
                select(func.max(Dimension.version)).where(
                    Dimension.organization_id == principal.organization_id,
                    Dimension.name == request.name,
                )
            )
            or 0
        ) + 1
        dimension = Dimension(
            organization_id=principal.organization_id,
            version=version,
            **request.model_dump(),
        )
        session.add(dimension)
        await session.flush()
    return {"id": str(dimension.id), "version": version}


@router.post("/data/time-definitions", status_code=status.HTTP_201_CREATED)
async def create_time_definition(
    request: CreateTimeDefinitionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        version = (
            await session.scalar(
                select(func.max(TimeDefinition.version)).where(
                    TimeDefinition.organization_id == principal.organization_id,
                    TimeDefinition.name == request.name,
                )
            )
            or 0
        ) + 1
        definition = TimeDefinition(
            organization_id=principal.organization_id,
            version=version,
            **request.model_dump(),
        )
        session.add(definition)
        await session.flush()
        await _audit_admin(
            session,
            principal,
            "data.time_definition.create",
            "time_definition",
            definition.id,
        )
    return {"id": str(definition.id), "version": version}


@router.post("/data/synonyms", status_code=status.HTTP_201_CREATED)
async def create_semantic_synonym(
    request: CreateSemanticSynonymRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "data.catalog.write")
    target_models: dict[str, Any] = {
        "METRIC": Metric,
        "DIMENSION": Dimension,
        "ENTITY": SemanticEntity,
        "RULE": BusinessRule,
    }
    async with session.begin():
        target_model = target_models[request.target_type]
        target_exists = await session.scalar(
            select(target_model.id).where(
                target_model.id == request.target_id,
                target_model.organization_id == principal.organization_id,
            )
        )
        if target_exists is None:
            raise NotFoundError("Semantic target", request.target_id)
        synonym = SemanticSynonym(
            organization_id=principal.organization_id,
            term=request.term.strip().casefold(),
            locale=request.locale.strip().lower(),
            target_type=request.target_type,
            target_id=request.target_id,
        )
        session.add(synonym)
        await session.flush()
        await _audit_admin(
            session,
            principal,
            "data.synonym.create",
            "semantic_synonym",
            synonym.id,
        )
    return {"id": str(synonym.id)}


@router.get("/agents")
async def list_agents(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    rows = (
        await session.execute(
            select(AgentDefinition, AgentVersion)
            .join(AgentVersion, AgentVersion.agent_id == AgentDefinition.id)
            .where(AgentDefinition.organization_id == principal.organization_id)
            .order_by(AgentDefinition.name, AgentVersion.version.desc())
        )
    ).all()
    latest: dict[UUID, dict[str, Any]] = {}
    for definition, version in rows:
        latest.setdefault(
            definition.id,
            {
                "id": str(definition.id),
                "version_id": str(version.id),
                "name": definition.name,
                "status": definition.status,
                "version": version.version,
                "spec": version.spec,
            },
        )
    return list(latest.values())


@router.get("/skills")
async def list_skills(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    rows = (
        await session.execute(
            select(SkillDefinition, SkillVersion)
            .join(SkillVersion, SkillVersion.skill_id == SkillDefinition.id)
            .where(SkillDefinition.organization_id == principal.organization_id)
            .order_by(SkillDefinition.name, SkillVersion.version.desc())
        )
    ).all()
    latest: dict[UUID, dict[str, Any]] = {}
    for definition, version in rows:
        latest.setdefault(
            definition.id,
            {
                "id": str(definition.id),
                "version_id": str(version.id),
                "name": definition.name,
                "status": definition.status,
                "version": version.version,
                "spec": version.spec,
            },
        )
    return list(latest.values())


@router.get("/costs")
async def list_costs(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal, "audit.read")
    rows = (
        await session.execute(
            select(
                ModelCall.operation,
                func.count(ModelCall.id),
                func.sum(ModelCall.input_tokens),
                func.sum(ModelCall.output_tokens),
                func.sum(ModelCall.cost_amount),
            )
            .where(ModelCall.organization_id == principal.organization_id)
            .group_by(ModelCall.operation)
            .order_by(ModelCall.operation)
        )
    ).all()
    return [
        {
            "operation": operation,
            "calls": calls,
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "cost_amount": str(cost_amount or 0),
        }
        for operation, calls, input_tokens, output_tokens, cost_amount in rows
    ]


@router.get("/prompts")
async def list_prompts(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal)
    rows = (
        await session.execute(
            select(PromptDefinition, PromptVersion)
            .join(PromptVersion, PromptVersion.prompt_id == PromptDefinition.id)
            .where(PromptDefinition.organization_id == principal.organization_id)
            .order_by(PromptDefinition.name, PromptVersion.version.desc())
        )
    ).all()
    latest: dict[UUID, dict[str, Any]] = {}
    for definition, version in rows:
        latest.setdefault(
            definition.id,
            {
                "id": str(definition.id),
                "version_id": str(version.id),
                "name": definition.name,
                "status": definition.status,
                "version": version.version,
                "checksum_sha256": version.checksum_sha256,
                "variables_schema": version.variables_schema,
            },
        )
    return list(latest.values())


@router.post("/prompts", status_code=status.HTTP_201_CREATED)
async def create_prompt_version(
    request: CreatePromptVersionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "prompts.write")
    _safe_configuration(request.variables_schema, "variables_schema")
    try:
        Draft202012Validator.check_schema(request.variables_schema)
    except SchemaError as exc:
        raise ValidationError(
            "prompt_variables_schema_invalid",
            "Prompt variables must use a valid JSON Schema",
            path=".".join(str(part) for part in exc.absolute_path),
        ) from exc
    if redact_text(request.template) != request.template:
        raise ValidationError(
            "prompt_secret_denied", "Prompt templates cannot contain credential material"
        )
    async with session.begin():
        definition = await session.scalar(
            select(PromptDefinition).where(
                PromptDefinition.organization_id == principal.organization_id,
                PromptDefinition.name == request.name,
            )
        )
        if definition is None:
            definition = PromptDefinition(
                organization_id=principal.organization_id,
                name=request.name,
                display_name=request.display_name,
                description=request.description,
                status=RegistryStatus.DRAFT,
            )
            session.add(definition)
            await session.flush()
        version_number = (
            await session.scalar(
                select(func.max(PromptVersion.version)).where(
                    PromptVersion.prompt_id == definition.id
                )
            )
            or 0
        ) + 1
        serialized = json.dumps(
            {"template": request.template, "variables_schema": request.variables_schema},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        version = PromptVersion(
            organization_id=principal.organization_id,
            prompt_id=definition.id,
            version=version_number,
            template=request.template,
            variables_schema=request.variables_schema,
            checksum_sha256=hashlib.sha256(serialized.encode()).hexdigest(),
            created_by=principal.id,
            created_at=utc_now(),
        )
        session.add(version)
        await session.flush()
        await _audit_admin(session, principal, "prompt.version.create", "prompt", definition.id)
    return {
        "id": str(definition.id),
        "version_id": str(version.id),
        "version": version.version,
        "status": definition.status,
    }


@router.get("/knowledge")
async def list_knowledge_metadata(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal)
    documents = await session.scalars(
        select(Document)
        .where(Document.organization_id == principal.organization_id)
        .order_by(Document.updated_at.desc())
        .limit(500)
    )
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "source": item.source,
            "external_id": item.external_id,
            "classification": item.classification,
            "current_version": item.current_version,
            "deleted": item.deleted_at is not None,
            "updated_at": item.updated_at,
        }
        for item in documents
    ]


@router.get("/secrets")
async def list_secret_metadata(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal, "secrets.metadata.read")
    references = await session.scalars(
        select(SecretReference)
        .where(SecretReference.organization_id == principal.organization_id)
        .order_by(SecretReference.name)
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "provider": item.provider,
            "description": item.description,
            "last_rotated_at": item.last_rotated_at,
            "has_encrypted_envelope": item.encrypted_envelope is not None,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
        for item in references
    ]


@router.post("/secrets", status_code=status.HTTP_201_CREATED)
async def create_secret_reference(
    request: CreateSecretReferenceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "secrets.metadata.write")
    async with session.begin():
        reference = SecretReference(
            organization_id=principal.organization_id,
            name=request.name,
            provider=request.provider,
            external_ref=request.external_ref,
            description=request.description,
            encrypted_envelope=None,
        )
        session.add(reference)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "secret_reference_exists", "A secret reference with this name already exists"
            ) from exc
        await _audit_admin(
            session,
            principal,
            "secret.reference.create",
            "secret_reference",
            reference.id,
        )
    return {"id": str(reference.id), "name": reference.name, "provider": reference.provider}


@router.get("/audit")
async def list_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal, "audit.read")
    records = await session.scalars(
        select(AuditRecord)
        .where(AuditRecord.organization_id == principal.organization_id)
        .order_by(AuditRecord.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(item.id),
            "actor_id": str(item.actor_id) if item.actor_id else None,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "outcome": item.outcome,
            "risk_level": item.risk_level,
            "metadata": item.redacted_metadata,
            "created_at": item.created_at,
        }
        for item in records
    ]
