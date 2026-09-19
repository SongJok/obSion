from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_capability_gateway
from obsion.api.schemas import (
    CapabilityDescriptorView,
    CapabilityInvokeRequest,
    CapabilityInvokeView,
)
from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest
from obsion.common.errors import ConflictError, NotFoundError
from obsion.db.models import CapabilityDefinition, CapabilityVersion
from obsion.domain.enums import DecisionEffect, RegistryStatus, RiskLevel, RunStatus
from obsion.registry.capability_descriptor import CapabilityDescriptor
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput
from obsion.security.workspace_access import require_run_access

router = APIRouter(tags=["capabilities"])
_policy = PolicyEngine()


def _capability_view(
    definition: CapabilityDefinition, version: CapabilityVersion
) -> CapabilityDescriptorView:
    return CapabilityDescriptorView.model_validate(
        CapabilityDescriptor.from_models(definition, version).as_view()
    )


async def _authorized_capabilities(
    session: AsyncSession,
    principal: Principal,
    capability_id: UUID | None = None,
) -> list[tuple[CapabilityDefinition, CapabilityVersion]]:
    """Authorize minimal envelopes before loading descriptor text or schemas."""

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
    if capability_id is not None:
        statement = statement.where(CapabilityDefinition.id == capability_id)
    envelopes = (await session.execute(statement)).all()
    authorized_versions: set[UUID] = set()
    seen: set[UUID] = set()
    for envelope in envelopes:
        if envelope.id in seen:
            continue
        seen.add(envelope.id)
        decision = await _policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action=envelope.permission_action,
                resource={
                    "id": str(envelope.id),
                    "version_id": str(envelope.version_id),
                    "name": envelope.name,
                    "classification": envelope.data_classification.value,
                },
                context={"operation": "capability_descriptor_read"},
                risk_level=RiskLevel.L1,
                resource_type="capability_descriptor",
                capability_version_id=envelope.version_id,
                resource_access_allowed=True,
            ),
        )
        if decision.effect == DecisionEffect.ALLOW:
            authorized_versions.add(envelope.version_id)
    if not authorized_versions:
        return []
    rows = (
        await session.execute(
            select(CapabilityDefinition, CapabilityVersion)
            .join(CapabilityVersion, CapabilityVersion.capability_id == CapabilityDefinition.id)
            .where(CapabilityVersion.id.in_(authorized_versions))
            .order_by(CapabilityDefinition.name)
        )
    ).all()
    return [row._tuple() for row in rows]


@router.get("/capabilities", response_model=list[CapabilityDescriptorView])
async def list_capabilities(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[CapabilityDescriptorView]:
    async with session.begin():
        rows = await _authorized_capabilities(session, principal)
    return [_capability_view(definition, version) for definition, version in rows]


@router.get("/capabilities/{capability_id}", response_model=CapabilityDescriptorView)
async def get_capability(
    capability_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> CapabilityDescriptorView:
    async with session.begin():
        rows = await _authorized_capabilities(session, principal, capability_id)
    if not rows:
        raise NotFoundError("Capability", capability_id)
    return _capability_view(*rows[0])


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
                payload=request.payload,
                resource=request.resource,
                environment=request.environment,
                # External callers cannot impersonate a registered Agent by choosing
                # an arbitrary display name in the request body.
                agent_name="external-client",
                run_id=request.run_id,
                step_id=request.step_id,
                capability_version=request.capability_version,
                capability_version_id=request.capability_version_id,
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
