from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    StudioCatalogView,
    StudioCompareRequest,
    StudioCompareView,
    StudioDocumentRequest,
    StudioPromoteRequest,
    StudioValidateView,
    StudioVersionView,
)
from obsion.application.studio import StudioService
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(prefix="/studio", tags=["studio"])


def get_studio_service() -> StudioService:
    return StudioService()


@router.get("/catalog", response_model=StudioCatalogView)
async def list_studio_catalog(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioCatalogView:
    payload = await service.catalog(session, principal)
    return StudioCatalogView.model_validate(payload)


@router.post("/validate", response_model=StudioValidateView)
async def validate_studio_document(
    request: StudioDocumentRequest,
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioValidateView:
    return StudioValidateView.model_validate(service.validate(principal, request.document))


@router.post("/agents", response_model=StudioVersionView)
async def publish_studio_agent(
    request: StudioDocumentRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioVersionView:
    async with session.begin():
        view, created = await service.publish(session, principal, "Agent", request.document)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return StudioVersionView.model_validate(view)


@router.post("/skills", response_model=StudioVersionView)
async def publish_studio_skill(
    request: StudioDocumentRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioVersionView:
    async with session.begin():
        view, created = await service.publish(session, principal, "Skill", request.document)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return StudioVersionView.model_validate(view)


@router.post("/promote", response_model=StudioVersionView)
async def promote_studio_version(
    request: StudioPromoteRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioVersionView:
    async with session.begin():
        view = await service.promote(
            session, principal, request.kind, request.name, request.version
        )
    return StudioVersionView.model_validate(view)


@router.post("/rollback", response_model=StudioVersionView)
async def rollback_studio_version(
    request: StudioPromoteRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioVersionView:
    async with session.begin():
        view = await service.rollback(
            session, principal, request.kind, request.name, request.version
        )
    return StudioVersionView.model_validate(view)


@router.post("/compare", response_model=StudioCompareView)
async def compare_studio_versions(
    request: StudioCompareRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: StudioService = Depends(get_studio_service),
) -> StudioCompareView:
    payload = await service.compare(
        session,
        principal,
        request.kind,
        request.name,
        request.baseline_version,
        request.candidate_version,
    )
    return StudioCompareView.model_validate(payload)
