from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import (
    get_im_delivery_service,
    get_im_identity_service,
    get_im_inbox_service,
)
from obsion.api.im_inbox_schemas import CreateImInstallation
from obsion.api.schemas import (
    AcceptTrustedImEventRequest,
    ClaimImDeliveryRequest,
    CompleteImDeliveryRequest,
    CreateImBindingRequest,
    CreateImConversationAudienceRequest,
    CreateImInstallationRequest,
    CreateImMessageRequest,
    FailImDeliveryRequest,
    ImBindingView,
    ImConversationAudienceView,
    ImDeliveryAttemptView,
    ImDeliveryClaimView,
    ImDeliveryPrepareView,
    ImDeliveryView,
    ImInstallationView,
    ImMessageAcceptedView,
    PrepareImDeliveryRequest,
    ReconcileImDeliveryRequest,
    TrustedImEventAcceptedView,
    UnknownImDeliveryRequest,
)
from obsion.application.im_delivery import ImDeliveryService
from obsion.application.im_identity import ImIdentityService
from obsion.application.im_inbox import ImInboxService
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

admin_router = APIRouter(prefix="/admin/im-bindings", tags=["administration"])
installation_router = APIRouter(prefix="/admin/im-installations", tags=["administration"])
audience_router = APIRouter(prefix="/admin/im-conversation-audiences", tags=["administration"])
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
            installation_id=request.installation_id,
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


@installation_router.get("", response_model=list[ImInstallationView])
async def list_im_installations(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> list[ImInstallationView]:
    installations = await service.list_installations(session, principal)
    return [ImInstallationView.model_validate(item) for item in installations]


@installation_router.post(
    "", response_model=ImInstallationView, status_code=status.HTTP_201_CREATED
)
async def create_im_installation(
    request: CreateImInstallationRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
    legacy_service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInstallationView:
    async with session.begin():
        if request.provider is not None:
            installation = await legacy_service.create_installation(
                session,
                principal,
                CreateImInstallation(
                    provider=request.provider,
                    external_corp_id=request.external_corp_id,
                    external_app_id=request.external_app_id,
                    connector_id=request.connector_id,
                    adapter_principal_id=request.adapter_principal_id,
                    verification_source=request.verification_source,
                ),
            )
        else:
            assert request.channel is not None
            assert request.installation_id is not None
            assert request.corp_id is not None
            assert request.app_key is not None
            installation = await service.install(
                session,
                principal,
                channel=request.channel,
                installation_id=request.installation_id,
                corp_id=request.corp_id,
                app_key=request.app_key,
            )
    return ImInstallationView.model_validate(installation)


@installation_router.post("/{installation_id}/revoke", response_model=ImInstallationView)
async def revoke_im_installation(
    installation_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> ImInstallationView:
    async with session.begin():
        installation = await service.revoke_installation(session, principal, installation_id)
    return ImInstallationView.model_validate(installation)


@audience_router.get("", response_model=list[ImConversationAudienceView])
async def list_im_conversation_audiences(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> list[ImConversationAudienceView]:
    audiences = await service.list_audiences(session, principal)
    return [ImConversationAudienceView.model_validate(item) for item in audiences]


@audience_router.post(
    "", response_model=ImConversationAudienceView, status_code=status.HTTP_201_CREATED
)
async def create_im_conversation_audience(
    request: CreateImConversationAudienceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> ImConversationAudienceView:
    async with session.begin():
        audience = await service.bind_conversation_audience(
            session,
            principal,
            installation_id=request.installation_id,
            conversation_id=request.conversation_id,
            workspace_id=request.workspace_id,
        )
    return ImConversationAudienceView.model_validate(audience)


@audience_router.post("/{audience_id}/revoke", response_model=ImConversationAudienceView)
async def revoke_im_conversation_audience(
    audience_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> ImConversationAudienceView:
    async with session.begin():
        audience = await service.revoke_conversation_audience(session, principal, audience_id)
    return ImConversationAudienceView.model_validate(audience)


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
    "/trusted-events",
    response_model=TrustedImEventAcceptedView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_trusted_im_event(
    request: AcceptTrustedImEventRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> TrustedImEventAcceptedView:
    async with session.begin():
        accepted = await service.accept_trusted_event(
            session,
            principal,
            channel=request.channel,
            installation_id=request.installation_id,
            corp_id=request.corp_id,
            app_key=request.app_key,
            vendor_event_id=request.vendor_event_id,
            sender_id=request.sender_id,
            conversation_id=request.conversation_id,
            text=request.text,
            is_group=request.is_group,
        )
    return TrustedImEventAcceptedView.model_validate(accepted)


@experience_router.get("/trusted-events/{event_id}", response_model=TrustedImEventAcceptedView)
async def get_trusted_im_event_status(
    event_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImIdentityService = Depends(get_im_identity_service),
) -> TrustedImEventAcceptedView:
    return TrustedImEventAcceptedView.model_validate(
        await service.get_inbox_status(session, principal, event_id)
    )


@experience_router.post(
    "/runs/{run_id}/deliveries",
    response_model=ImDeliveryPrepareView,
)
async def prepare_im_delivery(
    run_id: UUID,
    request: PrepareImDeliveryRequest | None = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryPrepareView:
    async with session.begin():
        delivery = await service.prepare(
            session,
            principal,
            run_id,
            worker_id=request.worker_id if request is not None else None,
        )
    return ImDeliveryPrepareView.model_validate(delivery)


@experience_router.post(
    "/deliveries/claims",
    response_model=ImDeliveryClaimView | None,
)
async def claim_im_delivery(
    request: ClaimImDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryClaimView | None:
    async with session.begin():
        delivery = await service.claim(
            session,
            principal,
            channel=request.channel,
            worker_id=request.worker_id,
        )
    return ImDeliveryClaimView.model_validate(delivery) if delivery is not None else None


@experience_router.get(
    "/deliveries/{delivery_id}",
    response_model=ImDeliveryView,
)
async def get_im_delivery(
    delivery_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryView:
    return ImDeliveryView.model_validate(await service.get(session, principal, delivery_id))


@experience_router.get(
    "/deliveries/{delivery_id}/attempts",
    response_model=list[ImDeliveryAttemptView],
)
async def list_im_delivery_attempts(
    delivery_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> list[ImDeliveryAttemptView]:
    attempts = await service.list_attempts(session, principal, delivery_id)
    return [ImDeliveryAttemptView.model_validate(item) for item in attempts]


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
            claim_generation=request.claim_generation,
            worker_id=request.worker_id,
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
            claim_generation=request.claim_generation,
            worker_id=request.worker_id,
            retryable=request.retryable,
            retry_after_seconds=request.retry_after_seconds,
        )
    return ImDeliveryView.model_validate(delivery)


@experience_router.post(
    "/deliveries/{delivery_id}/unknown",
    response_model=ImDeliveryView,
)
async def mark_im_delivery_unknown(
    delivery_id: UUID,
    request: UnknownImDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryView:
    async with session.begin():
        delivery = await service.mark_unknown(
            session,
            principal,
            delivery_id,
            claim_generation=request.claim_generation,
            worker_id=request.worker_id,
        )
    return ImDeliveryView.model_validate(delivery)


@experience_router.post(
    "/deliveries/{delivery_id}/reconcile",
    response_model=ImDeliveryView,
)
async def reconcile_im_delivery(
    delivery_id: UUID,
    request: ReconcileImDeliveryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImDeliveryService = Depends(get_im_delivery_service),
) -> ImDeliveryView:
    async with session.begin():
        delivery = await service.reconcile(
            session,
            principal,
            delivery_id,
            outcome=request.outcome,
            evidence=request.evidence,
            vendor_message_id=request.vendor_message_id,
        )
    return ImDeliveryView.model_validate(delivery)
