from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import AuthSessionView, CreateAuthSessionRequest
from obsion.config import Settings
from obsion.security.auth import (
    authenticate_principal,
    browser_session_cookie_secure,
    get_app_settings,
    get_principal,
    get_session,
    issue_auth_session,
    revoke_auth_session,
    validate_browser_session_origin,
)
from obsion.security.identity import Principal

public_router = APIRouter(prefix="/auth", tags=["authentication"])
router = APIRouter(prefix="/auth", tags=["authentication"])


@public_router.post(
    "/session",
    response_model=AuthSessionView,
    status_code=status.HTTP_201_CREATED,
)
async def create_browser_session(
    payload: CreateAuthSessionRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> AuthSessionView:
    validate_browser_session_origin(request, settings)
    prior_session_token = request.cookies.get(settings.auth_session_cookie_name)
    async with session.begin():
        principal = await authenticate_principal(session, settings, payload.access_token)
        await revoke_auth_session(session, prior_session_token)
        issued = await issue_auth_session(session, settings, principal)
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=issued.token,
        max_age=settings.auth_session_ttl_seconds,
        expires=issued.expires_at,
        path="/api/v1",
        secure=browser_session_cookie_secure(settings),
        httponly=True,
        samesite="strict",
    )
    _disable_caching(response)
    return _session_view(principal)


@public_router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def delete_browser_session(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> None:
    validate_browser_session_origin(request, settings)
    session_token = request.cookies.get(settings.auth_session_cookie_name)
    async with session.begin():
        await revoke_auth_session(session, session_token)
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        path="/api/v1",
        secure=browser_session_cookie_secure(settings),
        httponly=True,
        samesite="strict",
    )
    _disable_caching(response)


@router.get("/session", response_model=AuthSessionView)
async def get_browser_session(
    response: Response,
    principal: Principal = Depends(get_principal),
) -> AuthSessionView:
    _disable_caching(response)
    return _session_view(principal)


def _session_view(principal: Principal) -> AuthSessionView:
    return AuthSessionView(
        principal_id=principal.id,
        organization_id=principal.organization_id,
        display_name=principal.display_name,
        department=principal.department,
        roles=sorted(principal.roles),
    )


def _disable_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
