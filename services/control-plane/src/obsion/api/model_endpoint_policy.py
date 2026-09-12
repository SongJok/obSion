"""Change model processing scope without exposing or replacing endpoint credentials."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.admin_schemas import AdminModel
from obsion.common.errors import AuthorizationError, NotFoundError
from obsion.common.ids import new_id
from obsion.db.models import ModelEndpoint
from obsion.domain.enums import ActorType, Classification, DecisionEffect, RiskLevel
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput

router = APIRouter(prefix="/admin/models/endpoints", tags=["administration"])


class UpdateModelProcessingScope(AdminModel):
    classifications: list[Classification] = Field(min_length=1, max_length=4)


@router.patch("/{endpoint_id}/processing-scope")
async def update_model_processing_scope(
    endpoint_id: UUID,
    body: UpdateModelProcessingScope,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    resource = {"endpoint_id": str(endpoint_id)}
    classifications = list(dict.fromkeys(value.value for value in body.classifications))
    async with session.begin():
        decision = await PolicyEngine().evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action="models.write",
                resource=resource,
                context={"operation": "model.processing_scope.update"},
                risk_level=RiskLevel.L2,
                resource_type="model_endpoint",
            ),
        )
        allowed = principal.can("models.write") and decision.effect == DecisionEffect.ALLOW
        metadata: dict[str, Any] = {}
        if allowed:
            endpoint = await session.scalar(
                select(ModelEndpoint)
                .where(
                    ModelEndpoint.id == endpoint_id,
                    ModelEndpoint.organization_id == principal.organization_id,
                )
                .with_for_update()
            )
            if endpoint is None:
                raise NotFoundError("Model endpoint", endpoint_id)
            metadata = {"before": endpoint.classifications, "after": classifications}
            endpoint.classifications = classifications
        await AuditWriter().write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=new_id(),
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="model.processing_scope.update",
                resource_type="model_endpoint",
                resource_id=str(endpoint_id),
                outcome="SUCCESS" if allowed else "DENIED",
                policy_decision_id=decision.id,
                risk_level=RiskLevel.L2,
                resource=resource,
                metadata=metadata,
            ),
        )
    # Commit denial evidence before raising; a denied request never mutates scope.
    if not allowed:
        raise AuthorizationError("admin_access_denied", "Model scope changes are not permitted")
    return {"id": str(endpoint_id), "classifications": classifications}
