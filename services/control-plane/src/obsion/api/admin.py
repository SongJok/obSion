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
    CreateBusinessRuleRequest,
    CreateCapabilityBindingRequest,
    CreateColumnRequest,
    CreateConnectorRequest,
    CreateDataSourceRequest,
    CreateDepartmentRequest,
    CreateDimensionRequest,
    CreateMetricRequest,
    CreateModelEndpointRequest,
    CreateModelProfileRequest,
    CreatePolicyRequest,
    CreatePromptVersionRequest,
    CreateRoleRequest,
    CreateSecretReferenceRequest,
    CreateSemanticEntityRequest,
    CreateSemanticRelationRequest,
    CreateSemanticSynonymRequest,
    CreateTableRequest,
    CreateTimeDefinitionRequest,
    CreateUserRequest,
    ModelProfileBindingRequest,
    RoleBindingRequest,
)
from obsion.api.dependencies import get_connector_sdk_runtime
from obsion.application.slo import RuntimeSloService
from obsion.capabilities.connector_spi import ConnectorSdkRuntime
from obsion.capabilities.plugin_governance import (
    enforce_plugin_governance,
    inspect_plugin,
    is_spi_connector,
    merge_scan_into_health,
    plugin_requires_approval,
    promote_plugin,
)
from obsion.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ObsionError,
    ValidationError,
)
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
    Department,
    Dimension,
    Document,
    Metric,
    ModelCall,
    ModelEndpoint,
    ModelProfile,
    ModelProfileEndpoint,
    OperatorCapabilityInvocation,
    Policy,
    PromptDefinition,
    PromptVersion,
    Role,
    RunFeedback,
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
    ConnectorStatus,
    OperatorInvocationStatus,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.feedback.schemas import FeedbackSummaryView
from obsion.model_gateway.providers import SUPPORTED_PROVIDERS
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.egress import validate_model_endpoint
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text

router = APIRouter(prefix="/admin", tags=["administration"])
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)(?:$|[_-])",
    re.I,
)


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
    *,
    outcome: str = "SUCCESS",
    metadata: dict[str, Any] | None = None,
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
            outcome=outcome,
            metadata=metadata or {},
        ),
    )


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    _require_admin(principal)
    rows = await session.execute(
        select(User, Department)
        .outerjoin(
            Department,
            (Department.organization_id == User.organization_id)
            & (Department.id == User.department_id),
        )
        .where(User.organization_id == principal.organization_id)
        .order_by(User.display_name)
    )
    return [
        {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "department_id": str(department.id) if department is not None else None,
            "department": department.name if department is not None else None,
            "active": user.active,
        }
        for user, department in rows
    ]


@router.get("/departments")
async def list_departments(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal)
    rows = (
        await session.execute(
            select(Department, func.count(User.id))
            .outerjoin(
                User,
                (User.organization_id == Department.organization_id)
                & (User.department_id == Department.id)
                & User.active.is_(True),
            )
            .where(Department.organization_id == principal.organization_id)
            .group_by(
                Department.id,
                Department.organization_id,
                Department.name,
                Department.description,
                Department.parent_id,
                Department.active,
                Department.created_at,
                Department.updated_at,
            )
            .order_by(Department.name)
        )
    ).all()
    return [
        {
            "id": str(department.id),
            "name": department.name,
            "description": department.description,
            "parent_id": str(department.parent_id) if department.parent_id else None,
            "active": department.active,
            "active_user_count": count,
        }
        for department, count in rows
    ]


@router.post("/departments", status_code=status.HTTP_201_CREATED)
async def create_department(
    request: CreateDepartmentRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    _require_admin(principal, "identity.write")
    async with session.begin():
        if request.parent_id is not None:
            parent = await session.scalar(
                select(Department.id).where(
                    Department.id == request.parent_id,
                    Department.organization_id == principal.organization_id,
                    Department.active.is_(True),
                )
            )
            if parent is None:
                raise NotFoundError("Department", request.parent_id)
        department = Department(
            organization_id=principal.organization_id,
            name=request.name.strip(),
            description=request.description.strip(),
            parent_id=request.parent_id,
            active=True,
        )
        session.add(department)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "department_exists", "A department with this name already exists"
            ) from exc
        await _audit_admin(
            session, principal, "identity.department.create", "department", department.id
        )
    return {"id": str(department.id)}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    _require_admin(principal, "identity.write")
    async with session.begin():
        if request.department_id is not None:
            department = await session.scalar(
                select(Department.id).where(
                    Department.id == request.department_id,
                    Department.organization_id == principal.organization_id,
                    Department.active.is_(True),
                )
            )
            if department is None:
                raise NotFoundError("Department", request.department_id)
        user = User(
            organization_id=principal.organization_id,
            external_id=request.external_id,
            email=str(request.email),
            display_name=request.display_name,
            department_id=request.department_id,
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
        {
            "id": str(role.id),
            "name": role.name,
            "description": role.description,
            "permissions": role.permissions,
            "system": role.system,
        }
        for role in roles
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
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError("role_exists", "A role with this name already exists") from exc
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
    runtime: ConnectorSdkRuntime = Depends(get_connector_sdk_runtime),
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
            # Endpoint and connector configuration are gateway-only; the
            # browser receives health metadata rather than network targets.
            "has_credential": bool(item.credential_ref),
            "health": item.last_health,
            "spi": runtime.supports(item.connector_type),
            "plugin": inspect_plugin(item).as_dict(),
        }
        for item in connectors
    ]


@router.post("/connectors", status_code=status.HTTP_201_CREATED)
async def create_connector(
    request: CreateConnectorRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    runtime: ConnectorSdkRuntime = Depends(get_connector_sdk_runtime),
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
        if runtime.supports(connector.connector_type) or is_spi_connector(connector):
            scan = inspect_plugin(connector)
            if scan.error_code == "v1_production_action_boundary":
                raise ValidationError("v1_production_action_boundary", scan.message)
            if plugin_requires_approval(scan.risk) and connector.status == ConnectorStatus.ACTIVE:
                raise ObsionError(
                    "capability_denied",
                    "L3+ connector plugins cannot be created ACTIVE; scan, sign, and promote them",
                )
            if connector.status == ConnectorStatus.ACTIVE:
                enforce_plugin_governance(connector)
            connector.last_health = merge_scan_into_health(connector.last_health, scan)
        session.add(connector)
        await session.flush()
        await _audit_admin(session, principal, "connector.create", "connector", connector.id)
    return {"id": str(connector.id)}


@router.post("/connectors/{connector_id}/health")
async def probe_connector_health(
    connector_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    runtime: ConnectorSdkRuntime = Depends(get_connector_sdk_runtime),
) -> dict:
    _require_admin(principal)
    async with session.begin():
        connector = await session.get(Connector, connector_id)
        if connector is None or connector.organization_id != principal.organization_id:
            raise NotFoundError("Connector", connector_id)
        if not runtime.supports(connector.connector_type):
            await _audit_admin(
                session,
                principal,
                "connector.health",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": "connector_handler_missing"},
            )
            raise ValidationError(
                "connector_handler_missing",
                "No Connector SDK adapter is registered for this connector type",
                connector_type=connector.connector_type,
            )
        try:
            health = await runtime.probe_health(connector)
        except ObsionError as exc:
            connector.last_health = merge_scan_into_health(
                {
                    "status": "unavailable",
                    "adapter": "connector-sdk",
                    "error_code": exc.code,
                },
                inspect_plugin(connector),
            )
            await _audit_admin(
                session,
                principal,
                "connector.health",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": exc.code},
            )
            raise
        health_payload = merge_scan_into_health(health, inspect_plugin(connector))
        connector.last_health = health_payload
        await _audit_admin(session, principal, "connector.health", "connector", connector.id)
    return {"id": str(connector_id), "health": health_payload}


@router.post("/connectors/{connector_id}/discover")
async def discover_connector(
    connector_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    runtime: ConnectorSdkRuntime = Depends(get_connector_sdk_runtime),
) -> dict:
    _require_admin(principal)
    async with session.begin():
        connector = await session.get(Connector, connector_id)
        if connector is None or connector.organization_id != principal.organization_id:
            raise NotFoundError("Connector", connector_id)
        if not runtime.supports(connector.connector_type):
            await _audit_admin(
                session,
                principal,
                "connector.discover",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": "connector_handler_missing"},
            )
            raise ValidationError(
                "connector_handler_missing",
                "No Connector SDK adapter is registered for this connector type",
                connector_type=connector.connector_type,
            )
        binding_count = await session.scalar(
            select(func.count())
            .select_from(CapabilityBinding)
            .where(
                CapabilityBinding.organization_id == principal.organization_id,
                CapabilityBinding.connector_id == connector.id,
            )
        )
        try:
            discovery = await runtime.discover(connector)
        except ObsionError as exc:
            await _audit_admin(
                session,
                principal,
                "connector.discover",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": exc.code},
            )
            raise
        await _audit_admin(session, principal, "connector.discover", "connector", connector.id)
    return {
        "id": str(connector_id),
        "discovery": discovery,
        "binding_count": int(binding_count or 0),
    }


@router.post("/connectors/{connector_id}/scan")
async def scan_connector_plugin(
    connector_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    runtime: ConnectorSdkRuntime = Depends(get_connector_sdk_runtime),
) -> dict:
    _require_admin(principal)
    async with session.begin():
        connector = await session.get(Connector, connector_id)
        if connector is None or connector.organization_id != principal.organization_id:
            raise NotFoundError("Connector", connector_id)
        scan = inspect_plugin(connector)
        if not runtime.supports(connector.connector_type) and scan.status != "not_applicable":
            await _audit_admin(
                session,
                principal,
                "connector.plugin.scan",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": "connector_handler_missing"},
            )
            raise ValidationError(
                "connector_handler_missing",
                "No Connector SDK adapter is registered for this connector type",
                connector_type=connector.connector_type,
            )
        health = connector.last_health if isinstance(connector.last_health, dict) else {}
        connector.last_health = merge_scan_into_health(health, scan)
        outcome = "SUCCESS" if scan.status != "failed" else "FAILED"
        await _audit_admin(
            session,
            principal,
            "connector.plugin.scan",
            "connector",
            connector.id,
            outcome=outcome,
            metadata={"status": scan.status, "lifecycle": scan.lifecycle},
        )
    return {"id": str(connector_id), "scan": scan.as_dict()}


@router.post("/connectors/{connector_id}/promote")
async def promote_connector_plugin(
    connector_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    runtime: ConnectorSdkRuntime = Depends(get_connector_sdk_runtime),
) -> dict:
    _require_admin(principal, "connectors.write")
    async with session.begin():
        connector = await session.get(Connector, connector_id)
        if connector is None or connector.organization_id != principal.organization_id:
            raise NotFoundError("Connector", connector_id)
        if not runtime.supports(connector.connector_type) and not is_spi_connector(connector):
            await _audit_admin(
                session,
                principal,
                "connector.plugin.promote",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": "connector_handler_missing"},
            )
            raise ValidationError(
                "connector_handler_missing",
                "No Connector SDK adapter is registered for this connector type",
                connector_type=connector.connector_type,
            )
        try:
            scan = promote_plugin(connector, principal)
        except ObsionError as exc:
            await _audit_admin(
                session,
                principal,
                "connector.plugin.promote",
                "connector",
                connector.id,
                outcome="FAILED",
                metadata={"error_code": exc.code},
            )
            raise
        health = connector.last_health if isinstance(connector.last_health, dict) else {}
        connector.last_health = merge_scan_into_health(health, scan)
        await _audit_admin(
            session,
            principal,
            "connector.plugin.promote",
            "connector",
            connector.id,
            metadata={"lifecycle": scan.lifecycle, "risk": scan.risk},
        )
        status = connector.status
        payload = scan.as_dict()
    return {
        "id": str(connector_id),
        "status": status,
        "scan": payload,
    }


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
        if connector.environment != request.environment:
            raise ValidationError(
                "connector_environment_mismatch",
                "The connector and capability binding environments must match",
            )
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


@router.post("/models/profiles", status_code=status.HTTP_201_CREATED)
async def create_model_profile(
    request: CreateModelProfileRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, str]:
    _require_admin(principal, "models.write")
    requirements = request.requirements.model_dump(mode="json")
    routing_policy = request.routing_policy.model_dump(mode="json")
    _safe_configuration(requirements, "requirements")
    _safe_configuration(routing_policy, "routing_policy")
    unsupported = {
        provider.casefold()
        for provider in request.requirements.providers
        if provider.casefold() not in SUPPORTED_PROVIDERS
    }
    if unsupported:
        raise ValidationError(
            "model_endpoint_invalid",
            "Model profile references an unsupported provider protocol",
            providers=sorted(unsupported),
        )
    async with session.begin():
        profile = ModelProfile(
            organization_id=principal.organization_id,
            name=request.name,
            requirements=requirements,
            routing_policy=routing_policy,
            enabled=request.enabled,
        )
        session.add(profile)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "model_profile_exists", "A model profile with this name already exists"
            ) from exc
        await _audit_admin(
            session,
            principal,
            "model.profile.create",
            "model_profile",
            profile.id,
        )
    return {"id": str(profile.id), "name": profile.name}


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
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "requirements": item.requirements,
            "routing_policy": item.routing_policy,
            "enabled": item.enabled,
        }
        for item in profiles
    ]


@router.get("/models/endpoints")
async def list_model_endpoints(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    _require_admin(principal)
    endpoints = await session.scalars(
        select(ModelEndpoint)
        .where(ModelEndpoint.organization_id == principal.organization_id)
        .order_by(ModelEndpoint.name)
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "provider": item.provider,
            "model_id": item.model_id,
            # The egress base URL is intentionally omitted from browser-facing
            # administration responses; it is resolved only by ModelGateway.
            # Credential references are gateway-only configuration.  The admin
            # list exposes presence, never the reference value, so a browser
            # cannot turn this endpoint into a secret/configuration oracle.
            "has_credential": bool(item.credential_ref),
            "region": item.region,
            "classifications": item.classifications,
            "capabilities": item.capabilities,
            "limits": item.limits,
            "enabled": item.enabled,
        }
        for item in endpoints
    ]


@router.post("/models/endpoints", status_code=status.HTTP_201_CREATED)
async def create_model_endpoint(
    request: CreateModelEndpointRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    _require_admin(principal, "models.write")
    _safe_endpoint(request.base_url)
    provider = request.provider.casefold()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValidationError(
            "model_endpoint_invalid",
            "Model endpoint provider protocol is not supported",
            provider=provider,
        )
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
            provider=provider,
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
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "model_endpoint_exists", "A model endpoint with this name already exists"
            ) from exc
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
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "model_profile_binding_exists",
                "This model endpoint is already bound to the profile",
            ) from exc
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


@router.post("/data/entities", status_code=status.HTTP_201_CREATED)
async def create_semantic_entity(
    request: CreateSemanticEntityRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
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
                select(func.max(SemanticEntity.version)).where(
                    SemanticEntity.organization_id == principal.organization_id,
                    SemanticEntity.name == request.name,
                )
            )
            or 0
        ) + 1
        entity = SemanticEntity(
            organization_id=principal.organization_id,
            version=version,
            **request.model_dump(),
        )
        session.add(entity)
        await session.flush()
        await _audit_admin(session, principal, "data.entity.create", "semantic_entity", entity.id)
    return {"id": str(entity.id), "version": version}


@router.post("/data/relations", status_code=status.HTTP_201_CREATED)
async def create_semantic_relation(
    request: CreateSemanticRelationRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        entities = list(
            await session.scalars(
                select(SemanticEntity).where(
                    SemanticEntity.organization_id == principal.organization_id,
                    SemanticEntity.id.in_([request.source_entity_id, request.target_entity_id]),
                )
            )
        )
        if len(entities) != len({request.source_entity_id, request.target_entity_id}):
            raise NotFoundError("Semantic entity", request.source_entity_id)
        relation = SemanticRelation(
            organization_id=principal.organization_id,
            **request.model_dump(),
        )
        session.add(relation)
        await session.flush()
        await _audit_admin(
            session, principal, "data.relation.create", "semantic_relation", relation.id
        )
    return {"id": str(relation.id)}


@router.post("/data/rules", status_code=status.HTTP_201_CREATED)
async def create_business_rule(
    request: CreateBusinessRuleRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "data.catalog.write")
    async with session.begin():
        version = (
            await session.scalar(
                select(func.max(BusinessRule.version)).where(
                    BusinessRule.organization_id == principal.organization_id,
                    BusinessRule.name == request.name,
                )
            )
            or 0
        ) + 1
        rule = BusinessRule(
            organization_id=principal.organization_id,
            version=version,
            **request.model_dump(),
        )
        session.add(rule)
        await session.flush()
        await _audit_admin(session, principal, "data.rule.create", "business_rule", rule.id)
    return {"id": str(rule.id), "version": version}


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


@router.get("/slo")
async def project_runtime_slo(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal, "audit.read")
    return await RuntimeSloService().project(session, principal.organization_id)


@router.get("/feedback/summary", response_model=FeedbackSummaryView)
async def summarize_run_feedback(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> FeedbackSummaryView:
    _require_admin(principal, "audit.read")
    rows = (
        await session.execute(
            select(RunFeedback.rating, func.count(RunFeedback.id))
            .where(RunFeedback.organization_id == principal.organization_id)
            .group_by(RunFeedback.rating)
        )
    ).all()
    counts = {str(rating): count for rating, count in rows}
    helpful = counts.get("HELPFUL", 0)
    needs_improvement = counts.get("NEEDS_IMPROVEMENT", 0)
    total = helpful + needs_improvement
    return FeedbackSummaryView(
        total=total,
        helpful=helpful,
        needs_improvement=needs_improvement,
        helpful_rate=helpful / total if total else None,
    )


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
            "correlation_id": str(item.correlation_id),
            "actor_type": item.actor_type,
            "actor_id": str(item.actor_id) if item.actor_id else None,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "outcome": item.outcome,
            "risk_level": item.risk_level,
            "policy_decision_id": (
                str(item.policy_decision_id) if item.policy_decision_id else None
            ),
            "approval_id": str(item.approval_id) if item.approval_id else None,
            "metadata": item.redacted_metadata,
            "latency_ms": item.latency_ms,
            "created_at": item.created_at,
        }
        for item in records
    ]


@router.get("/operator-invocations")
async def list_operator_invocations(
    invocation_status: OperatorInvocationStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    """列出无 Run Capability 幂等账本；不返回输入或结果内容。"""

    _require_admin(principal, "audit.read")
    statement = select(OperatorCapabilityInvocation).where(
        OperatorCapabilityInvocation.organization_id == principal.organization_id
    )
    if invocation_status is not None:
        statement = statement.where(OperatorCapabilityInvocation.status == invocation_status)
    records = await session.scalars(
        statement.order_by(OperatorCapabilityInvocation.created_at.desc()).limit(limit)
    )
    return [
        {
            "id": str(item.id),
            "request_id": str(item.request_id),
            "principal_id": str(item.principal_id),
            "capability_name": item.capability_name,
            "capability_version_id": str(item.capability_version_id),
            "connector_id": str(item.connector_id),
            "policy_decision_id": str(item.policy_decision_id),
            "status": item.status,
            "error_code": item.error_code,
            "reconciliation_required": item.status == OperatorInvocationStatus.UNKNOWN,
            "lease_expires_at": item.lease_expires_at,
            "created_at": item.created_at,
            "completed_at": item.completed_at,
            "expires_at": item.expires_at,
        }
        for item in records
    ]
