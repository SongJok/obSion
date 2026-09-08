from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_dingtalk_outbox_service, get_im_inbox_service
from obsion.api.im_inbox_schemas import (
    CreateImGroupAudience,
    CreateImInstallation,
    CreateInstallationBinding,
    DingTalkOutboxView,
    ImGroupAudienceView,
    ImInboxView,
    ImInstallationBindingView,
    ImInstallationView,
    TrustedImInbound,
)
from obsion.application.dingtalk_outbox import DingTalkOutboxService
from obsion.application.im_inbox import ImInboxService
from obsion.common.errors import AuthorizationError, ConflictError
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["trusted-im"])
_ADMIN = "/admin/im-installations"
_INBOX = "/experience/im/installations/{installation_id}/inbox"


@router.get(
    "/admin/im-dingtalk/outbox",
    response_model=list[DingTalkOutboxView],
    tags=["administration"],
)
async def list_dingtalk_outbox(
    limit: int = Query(default=100, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: DingTalkOutboxService = Depends(get_dingtalk_outbox_service),
) -> list[DingTalkOutboxView]:
    async with session.begin():
        rows = await service.list_for_admin(session, principal, limit=limit)
    return [DingTalkOutboxView.model_validate(row) for row in rows]


@router.post(
    "/admin/im-dingtalk/outbox/{outbox_id}/reconcile",
    response_model=DingTalkOutboxView,
    tags=["administration"],
)
async def reconcile_dingtalk_outbox(
    outbox_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: DingTalkOutboxService = Depends(get_dingtalk_outbox_service),
) -> DingTalkOutboxView:
    """Run one governed, read-only vendor receipt query for an accepted row."""

    if not principal.can("admin.read"):
        raise AuthorizationError("admin_access_denied", "Administration access is not permitted")
    # The claim is committed before the second transaction performs vendor I/O.
    async with session.begin():
        claim = await service.claim_reconciliation(
            session,
            outbox_id=outbox_id,
            organization_id=principal.organization_id,
        )
    if claim is None:
        raise ConflictError(
            "im_delivery_receipt_conflict",
            "The DingTalk delivery is not awaiting reconciliation",
        )
    async with session.begin():
        outbox = await service.reconcile(session, claim)
    if outbox is None:
        raise ConflictError(
            "im_delivery_receipt_conflict",
            "The DingTalk reconciliation claim is stale",
        )
    return DingTalkOutboxView.model_validate(outbox)


@router.get(_ADMIN, response_model=list[ImInstallationView])
async def list_installations(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> list[ImInstallationView]:
    async with session.begin():
        installations = await service.list_installations(session, principal)
    return [ImInstallationView.model_validate(item) for item in installations]


@router.post(_ADMIN, response_model=ImInstallationView, status_code=201)
async def create_installation(
    request: CreateImInstallation,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInstallationView:
    async with session.begin():
        installation = await service.create_installation(session, principal, request)
    return ImInstallationView.model_validate(installation)


@router.post(_ADMIN + "/{installation_id}/revoke", response_model=ImInstallationView)
async def revoke_installation(
    installation_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInstallationView:
    async with session.begin():
        installation = await service.revoke_installation(session, principal, installation_id)
    return ImInstallationView.model_validate(installation)


@router.post(
    _ADMIN + "/{installation_id}/bindings",
    response_model=ImInstallationBindingView,
    status_code=201,
)
async def bind_sender(
    installation_id: UUID,
    request: CreateInstallationBinding,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInstallationBindingView:
    async with session.begin():
        binding = await service.bind(session, principal, installation_id, request)
    return ImInstallationBindingView.model_validate(binding)


@router.post(
    _ADMIN + "/{installation_id}/bindings/{binding_id}/revoke",
    response_model=ImInstallationBindingView,
)
async def revoke_sender(
    installation_id: UUID,
    binding_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInstallationBindingView:
    async with session.begin():
        binding = await service.revoke_binding(session, principal, installation_id, binding_id)
    return ImInstallationBindingView.model_validate(binding)


@router.get(
    _ADMIN + "/{installation_id}/group-audiences",
    response_model=list[ImGroupAudienceView],
    tags=["administration"],
)
async def list_group_audiences(
    installation_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> list[ImGroupAudienceView]:
    async with session.begin():
        audiences = await service.list_group_audiences(session, principal, installation_id)
    return [ImGroupAudienceView.model_validate(item) for item in audiences]


@router.post(
    _ADMIN + "/{installation_id}/group-audiences",
    response_model=ImGroupAudienceView,
    status_code=201,
    tags=["administration"],
)
async def upsert_group_audience(
    installation_id: UUID,
    request: CreateImGroupAudience,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImGroupAudienceView:
    async with session.begin():
        audience = await service.upsert_group_audience(session, principal, installation_id, request)
    return ImGroupAudienceView.model_validate(audience)


@router.post(
    _ADMIN + "/{installation_id}/group-audiences/{audience_id}/revoke",
    response_model=ImGroupAudienceView,
    tags=["administration"],
)
async def revoke_group_audience(
    installation_id: UUID,
    audience_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImGroupAudienceView:
    async with session.begin():
        audience = await service.revoke_group_audience(
            session, principal, installation_id, audience_id
        )
    return ImGroupAudienceView.model_validate(audience)


@router.post(_INBOX, response_model=ImInboxView, status_code=202)
async def receive_message(
    installation_id: UUID,
    request: TrustedImInbound,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInboxView:
    async with session.begin():
        inbox, conflict = await service.receive(session, principal, installation_id, request)
    # Commit precedes both durable acceptance and content-conflict rejection.
    if conflict:
        raise ConflictError("idempotency_key_reused", "Vendor event content does not match")
    return ImInboxView.model_validate(inbox)


@router.get(_INBOX, response_model=list[ImInboxView])
async def list_messages(
    installation_id: UUID,
    status: Literal["RECEIVED", "PROCESSED"] = "RECEIVED",
    limit: int = Query(default=20, ge=1, le=100),
    after: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> list[ImInboxView]:
    async with session.begin():
        inboxes = await service.list_messages(
            session,
            principal,
            installation_id,
            status=status,
            limit=limit,
            after=after,
        )
    return [ImInboxView.model_validate(inbox) for inbox in inboxes]


@router.get(_INBOX + "/{inbox_id}", response_model=ImInboxView)
async def get_message(
    installation_id: UUID,
    inbox_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInboxView:
    async with session.begin():
        inbox = await service.get(session, principal, installation_id, inbox_id)
    return ImInboxView.model_validate(inbox)


@router.post(_INBOX + "/{inbox_id}/process", response_model=ImInboxView)
async def process_message(
    installation_id: UUID,
    inbox_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ImInboxService = Depends(get_im_inbox_service),
) -> ImInboxView:
    async with session.begin():
        inbox = await service.process(session, principal, installation_id, inbox_id)
    return ImInboxView.model_validate(inbox)
