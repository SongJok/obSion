from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_capability_gateway
from obsion.api.schemas import CapabilityInvokeRequest, CapabilityInvokeView
from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest
from obsion.common.errors import ConflictError, NotFoundError
from obsion.db.models import CapabilityDefinition, CapabilityVersion
from obsion.domain.enums import RegistryStatus, RunStatus
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access

router = APIRouter(tags=["capabilities"])


def _capability_view(
    definition: CapabilityDefinition, version: CapabilityVersion
) -> dict[str, object]:
    return {
        "id": str(definition.id),
        "version_id": str(version.id),
        "name": definition.name,
        "display_name": definition.display_name,
        "description": definition.description,
        "version": version.version,
        "transport": version.transport,
        "risk": version.risk_level,
        "side_effect": version.side_effect,
        "permission": version.permission_action,
        "input_schema": version.input_schema,
        "output_schema": version.output_schema,
    }


@router.get("/capabilities")
async def list_capabilities(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(CapabilityDefinition, CapabilityVersion)
            .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
            .where(
                CapabilityDefinition.organization_id == principal.organization_id,
                CapabilityDefinition.status == RegistryStatus.ACTIVE,
            )
            .order_by(CapabilityDefinition.name, CapabilityVersion.version.desc())
        )
    ).all()
    visible: list[dict[str, object]] = []
    seen: set[object] = set()
    for definition, version in rows:
        if definition.id in seen or not principal.can(version.permission_action):
            continue
        visible.append(_capability_view(definition, version))
        seen.add(definition.id)
    return visible


@router.get("/capabilities/{capability_id}")
async def get_capability(
    capability_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, object]:
    row = (
        await session.execute(
            select(CapabilityDefinition, CapabilityVersion)
            .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
            .where(
                CapabilityDefinition.id == capability_id,
                CapabilityDefinition.organization_id == principal.organization_id,
                CapabilityDefinition.status == RegistryStatus.ACTIVE,
            )
            .order_by(CapabilityVersion.version.desc())
            .limit(1)
        )
    ).one_or_none()
    if row is None or not principal.can(row[1].permission_action):
        raise NotFoundError("Capability", capability_id)
    return _capability_view(*row._tuple())


@router.post("/capabilities/{capability_name}/invoke", response_model=CapabilityInvokeView)
async def invoke_capability(
    capability_name: str,
    request: CapabilityInvokeRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> CapabilityInvokeView:
    async with session.begin():
        run = await require_run_access(
            session,
            principal,
            request.run_id,
            write=True,
        )
        if run.status not in {
            RunStatus.PENDING,
            RunStatus.RUNNING,
            RunStatus.WAITING_APPROVAL,
        }:
            raise ConflictError(
                "run_not_invocable",
                "Capabilities may only be invoked for an active run",
                status=run.status,
            )
        result = await gateway.invoke(
            session,
            GatewayRequest(
                principal=principal,
                capability_name=capability_name,
                capability_version=request.capability_version,
                payload=request.payload,
                resource=request.resource,
                environment=request.environment,
                agent_name=request.agent_name,
                run_id=request.run_id,
                step_id=request.step_id,
            ),
        )
    return CapabilityInvokeView(
        status=result.status,
        policy_decision_id=result.policy_decision_id,
        output=result.output,
        evidence_id=result.evidence_id,
        approval_id=result.approval_id,
        error_code=result.error_code,
        error_message=result.error_message,
        capability_version_id=result.capability_version_id,
        connector_id=result.connector_id,
    )
