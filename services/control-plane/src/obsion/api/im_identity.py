from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_im_delivery_service, get_im_identity_service
from obsion.api.schemas import (
    CompleteImDeliveryRequest,
    CreateImBindingRequest,
    CreateImMessageRequest,
    FailImDeliveryRequest,
    ImBindingView,
    ImDeliveryPrepareView,
    ImDeliveryView,
    ImMessageAcceptedView,
)
from obsion.application.im_delivery import ImDeliveryService
from obsion.application.im_identity import ImIdentityService
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

admin_router = APIRouter(prefix="/admin/im-bindings", tags=["administration"])
experience_router = APIRouter(prefix="/experience/im", tags=["experience"])


@admin_router.get("", response_model=list[ImBindingView])
async def list_im_bindings(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> list[ImBindingView]:
    bindings = await service.list_bindings(session, principal)
    return [ImBindingView.model_validate(item) for item in bindings]


@admin_router.post("", response_model=ImBindingView, status_code=status.HTTP_201_CREATED)
async def create_im_binding(
    request: CreateImBindingRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> ImBindingView:
    async with session.begin():
        binding = await service.bind(
            session,
            principal,
            channel=request.channel,
            sender_id=request.sender_id,
            user_id=request.user_id,
        )
    return ImBindingView.model_validate(binding)


@admin_router.post("/{binding_id}/revoke", response_model=ImBindingView)
async def revoke_im_binding(
    binding_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> ImBindingView:
    async with session.begin():
        binding = await service.revoke(session, principal, binding_id)
    return ImBindingView.model_validate(binding)


@experience_router.post(
    "/messages",
    response_model=ImMessageAcceptedView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_im_message(
    request: CreateImMessageRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> ImMessageAcceptedView:
    async with session.begin():
        accepted = await service.ingest_message(
            session,
            principal,
            channel=request.channel,
            sender_id=request.sender_id,
            conversation_id=request.conversation_id,
            text=request.text,
        )
    return ImMessageAcceptedView.model_validate(accepted)


@experience_router.post(
    "/runs/{run_id}/deliveries",
    response_model=ImDeliveryPrepareView,
)
async def prepare_im_delivery(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryPrepareView:
    async with session.begin():
        delivery = await service.prepare(session, principal, run_id)
    return ImDeliveryPrepareView.model_validate(delivery)


@experience_router.post(
    "/deliveries/{delivery_id}/complete",
    response_model=ImDeliveryView,
)
async def complete_im_delivery(
    delivery_id: UUID,
    request: CompleteImDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryView:
    async with session.begin():
        delivery = await service.complete(
            session,
            principal,
            delivery_id,
            vendor_message_id=request.vendor_message_id,
        )
    return ImDeliveryView.model_validate(delivery)


@experience_router.post(
    "/deliveries/{delivery_id}/fail",
    response_model=ImDeliveryView,
)
async def fail_im_delivery(
    delivery_id: UUID,
    request: FailImDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryView:
    async with session.begin():
        delivery = await service.fail(
            session,
            principal,
            delivery_id,
            failure_code=request.failure_code,
        )
    return ImDeliveryView.model_validate(delivery)
