import asyncio
import secrets
from collections.abc import AsyncIterator
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import AuthorizationError
from obsion.config import AuthMode, Environment, Settings
from obsion.db.models import Department, Role, User, UserRole
from obsion.persistence.auth_sessions import AuthSessionStore, IssuedAuthSession
from obsion.security.identity import Principal

_bearer = HTTPBearer(auto_error=False)
_documented_browser_session = APIKeyCookie(name="obsion_session", auto_error=False)
_auth_sessions = AuthSessionStore()
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        yield session


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def _load_principal(
    session: AsyncSession, organization_id: UUID, external_id: str
) -> Principal:
    result = await session.execute(
        select(User, Department, Role)
        .outerjoin(
            Department,
            (Department.organization_id == User.organization_id)
            & (Department.id == User.department_id),
        )
        .outerjoin(
            UserRole,
            (UserRole.organization_id == User.organization_id) & (UserRole.user_id == User.id),
        )
        .outerjoin(
            Role,
            (Role.organization_id == UserRole.organization_id) & (Role.id == UserRole.role_id),
        )
        .where(
            User.organization_id == organization_id,
            User.external_id == external_id,
            User.active.is_(True),
        )
    )
    rows = result.all()
    if not rows:
        raise AuthorizationError("unknown_principal", "The authenticated user is not provisioned")
    user = rows[0][0]
    department = rows[0][1]
    roles = frozenset(row[2].name for row in rows if row[2] is not None)
    permissions = frozenset(
        permission
        for row in rows
        if row[2] is not None
        for permission in row[2].permissions
        if isinstance(permission, str)
    )
    return Principal(
        id=user.id,
        organization_id=user.organization_id,
        external_id=user.external_id,
        display_name=user.display_name,
        department_id=department.id if department is not None else None,
        department=department.name if department is not None else None,
        roles=roles,
        permissions=permissions,
        attributes=user.attributes,
    )


async def load_principal_by_id(
    session: AsyncSession, organization_id: UUID, user_id: UUID
) -> Principal:
    user = await session.scalar(
        select(User).where(
            User.id == user_id,
            User.organization_id == organization_id,
            User.active.is_(True),
        )
    )
    if user is None:
        raise AuthorizationError("unknown_principal", "The run owner is not provisioned")
    return await _load_principal(session, organization_id, user.external_id)


def _decode_oidc_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.oidc_jwks_url or not settings.oidc_audience or not settings.oidc_issuer:
        raise AuthorizationError("oidc_not_configured", "OIDC authentication is not configured")
    client = PyJWKClient(str(settings.oidc_jwks_url), cache_jwk_set=True, lifespan=300)
    signing_key = client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=settings.oidc_algorithms,
        audience=settings.oidc_audience,
        issuer=str(settings.oidc_issuer),
        options={"require": ["exp", "iat", "sub"]},
    )
    return dict(claims)


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    documented_browser_session: str | None = Depends(_documented_browser_session),
    settings: Settings = Depends(get_app_settings),
) -> Principal:
    existing = getattr(request.state, "principal", None)
    if isinstance(existing, Principal):
        return existing
    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    async with request.app.state.database.sessions() as session:
        if token is not None:
            principal = await authenticate_principal(session, settings, token)
        else:
            session_token = request.cookies.get(settings.auth_session_cookie_name)
            if settings.auth_session_cookie_name == "obsion_session":
                session_token = documented_browser_session or session_token
            if session_token is None:
                raise AuthorizationError(
                    "authentication_required", "A bearer token or browser session is required"
                )
            validate_browser_session_origin(request, settings)
            principal = await authenticate_session_principal(session, session_token)
    request.state.principal = principal
    return principal


def validate_browser_session_origin(request: Request, settings: Settings) -> None:
    """Reject cross-origin unsafe requests that authenticate through a cookie.

    SameSite=Strict is the primary browser control. This server-side check keeps
    the boundary explicit and also covers clients with unusual cookie behavior.
    """
    if request.method.upper() in _SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and origin not in _allowed_session_origins(settings):
        raise AuthorizationError(
            "request_origin_denied", "The request origin is not allowed for this session"
        )
    if origin is None and request.headers.get("sec-fetch-site") == "cross-site":
        raise AuthorizationError(
            "request_origin_denied", "Cross-site session requests are not allowed"
        )


def browser_session_cookie_secure(settings: Settings) -> bool:
    return settings.environment in {Environment.STAGING, Environment.PRODUCTION}


async def issue_auth_session(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
) -> IssuedAuthSession:
    await _auth_sessions.purge_retained(
        session,
        retention_days=settings.auth_session_retention_days,
    )
    return await _auth_sessions.create(
        session,
        organization_id=principal.organization_id,
        user_id=principal.id,
        ttl_seconds=settings.auth_session_ttl_seconds,
    )


async def authenticate_session_principal(
    session: AsyncSession,
    session_token: str | None,
) -> Principal:
    if session_token is None:
        raise AuthorizationError(
            "authentication_required", "A bearer token or browser session is required"
        )
    auth_session = await _auth_sessions.resolve(session, session_token)
    if auth_session is None:
        raise AuthorizationError("invalid_token", "The browser session is invalid or expired")
    return await load_principal_by_id(
        session,
        auth_session.organization_id,
        auth_session.user_id,
    )


async def revoke_auth_session(session: AsyncSession, session_token: str | None) -> bool:
    if session_token is None:
        return False
    return await _auth_sessions.revoke(session, session_token)


def _allowed_session_origins(settings: Settings) -> set[str]:
    allowed = {value.rstrip("/") for value in settings.allowed_origins}
    public_url = urlsplit(str(settings.api_public_url))
    if public_url.scheme and public_url.netloc:
        allowed.add(f"{public_url.scheme}://{public_url.netloc}")
    return allowed


async def authenticate_principal(
    session: AsyncSession,
    settings: Settings,
    bearer_token: str | None,
) -> Principal:
    """Resolve one Principal for HTTP or a stateful App Server connection.

    The token is intentionally accepted as a value rather than a FastAPI dependency
    so protocol adapters share the exact same authentication and provisioning rules.
    """
    if bearer_token is None:
        raise AuthorizationError("authentication_required", "A bearer token is required")

    if settings.auth_mode == AuthMode.DEVELOPMENT:
        expected_token = settings.dev_bearer_token.get_secret_value()
        if not secrets.compare_digest(bearer_token, expected_token):
            raise AuthorizationError("invalid_token", "The bearer token is invalid")
        return await _load_principal(
            session, settings.dev_organization_id, str(settings.dev_user_id)
        )

    try:
        claims = await asyncio.to_thread(_decode_oidc_token, bearer_token, settings)
        organization_claim = claims.get("organization_id") or claims.get("org_id")
        if not organization_claim:
            raise AuthorizationError(
                "organization_claim_missing", "The identity token has no organization claim"
            )
        return await _load_principal(session, UUID(str(organization_claim)), str(claims["sub"]))
    except AuthorizationError:
        raise
    except (ValueError, jwt.PyJWTError) as exc:
        raise AuthorizationError("invalid_token", "The bearer token is invalid") from exc
