import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import AuthorizationError
from obsion.config import AuthMode, Settings
from obsion.db.models import Role, User, UserRole
from obsion.security.identity import Principal

_bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        yield session


def get_app_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def _load_principal(
    session: AsyncSession, organization_id: UUID, external_id: str
) -> Principal:
    result = await session.execute(
        select(User, Role)
        .outerjoin(UserRole, UserRole.user_id == User.id)
        .outerjoin(Role, Role.id == UserRole.role_id)
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
    roles = frozenset(row[1].name for row in rows if row[1] is not None)
    permissions = frozenset(
        permission
        for row in rows
        if row[1] is not None
        for permission in row[1].permissions
        if isinstance(permission, str)
    )
    return Principal(
        id=user.id,
        organization_id=user.organization_id,
        external_id=user.external_id,
        display_name=user.display_name,
        department=user.department,
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
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session, use_cache=False),
    settings: Settings = Depends(get_app_settings),
) -> Principal:
    if settings.auth_mode == AuthMode.DEVELOPMENT:
        return await _load_principal(
            session, settings.dev_organization_id, str(settings.dev_user_id)
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthorizationError("authentication_required", "A bearer token is required")
    try:
        claims = await asyncio.to_thread(_decode_oidc_token, credentials.credentials, settings)
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
