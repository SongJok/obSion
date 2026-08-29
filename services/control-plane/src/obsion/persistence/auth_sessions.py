import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.time import utc_now
from obsion.db.models import AuthSession


@dataclass(frozen=True, slots=True)
class IssuedAuthSession:
    token: str
    expires_at: datetime


class AuthSessionStore:
    """Persist and resolve revocable opaque sessions without retaining secrets."""

    async def create(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        user_id: UUID,
        ttl_seconds: int,
    ) -> IssuedAuthSession:
        token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(seconds=ttl_seconds)
        session.add(
            AuthSession(
                organization_id=organization_id,
                user_id=user_id,
                token_digest=self._digest(token),
                expires_at=expires_at,
            )
        )
        await session.flush()
        return IssuedAuthSession(token=token, expires_at=expires_at)

    async def resolve(
        self,
        session: AsyncSession,
        token: str,
    ) -> AuthSession | None:
        return cast(
            AuthSession | None,
            await session.scalar(
                select(AuthSession).where(
                    AuthSession.token_digest == self._digest(token),
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > utc_now(),
                )
            ),
        )

    async def revoke(self, session: AsyncSession, token: str) -> bool:
        result = cast(
            CursorResult[Any],
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.token_digest == self._digest(token),
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=utc_now(), updated_at=utc_now())
            ),
        )
        return bool(result.rowcount)

    async def purge_retained(
        self,
        session: AsyncSession,
        *,
        retention_days: int,
    ) -> int:
        cutoff = utc_now() - timedelta(days=retention_days)
        result = cast(
            CursorResult[Any],
            await session.execute(
                delete(AuthSession).where(
                    or_(
                        AuthSession.expires_at < cutoff,
                        AuthSession.revoked_at < cutoff,
                    )
                )
            ),
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
