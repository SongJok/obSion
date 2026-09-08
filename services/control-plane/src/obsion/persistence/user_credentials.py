"""Enrolment and verification of local password credentials.

Verification is deliberately indistinguishable across "no such user", "no
credential enrolled", and "wrong password": all three return the same failure so
the endpoint cannot be used to enumerate provisioned identities. Lockout state
lives on the credential row and is advanced inside the caller's transaction.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.ids import new_id
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import User, UserCredential
from obsion.security.passwords import (
    ALGORITHM,
    PasswordPolicy,
    ScryptParameters,
    hash_password,
    needs_rehash,
    verification_sentinel,
    verify_password,
)


class CredentialOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    LOCKED = "LOCKED"


@dataclass(frozen=True, slots=True)
class CredentialVerification:
    outcome: CredentialOutcome
    user_id: UUID | None = None
    organization_id: UUID | None = None
    must_change: bool = False
    locked_until: datetime | None = None

    @property
    def verified(self) -> bool:
        return self.outcome is CredentialOutcome.VERIFIED


class UserCredentialStore:
    def __init__(
        self,
        *,
        parameters: ScryptParameters | None = None,
        max_failed_attempts: int = 5,
        lockout_seconds: int = 900,
    ) -> None:
        self.parameters = parameters or ScryptParameters()
        self.max_failed_attempts = max_failed_attempts
        self.lockout_seconds = lockout_seconds

    async def enroll(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        user_id: UUID,
        plaintext: str,
        policy: PasswordPolicy,
        must_change: bool = False,
    ) -> UserCredential:
        """Create or rotate the single credential for one identity."""

        policy.enforce(plaintext)
        encoded = await self._hash(plaintext)
        now = utc_now()
        credential = await self._load(session, organization_id=organization_id, user_id=user_id)
        if credential is None:
            credential = UserCredential(
                id=new_id(),
                organization_id=organization_id,
                user_id=user_id,
                algorithm=ALGORITHM,
                encoded_secret=encoded,
                must_change=must_change,
                failed_attempts=0,
                rotated_at=now,
            )
            session.add(credential)
        else:
            credential.algorithm = ALGORITHM
            credential.encoded_secret = encoded
            credential.must_change = must_change
            credential.failed_attempts = 0
            credential.locked_until = None
            credential.rotated_at = now
            credential.updated_at = now
        await session.flush()
        return credential

    async def verify(
        self,
        session: AsyncSession,
        *,
        email: str,
        plaintext: str,
        organization_id: UUID | None = None,
    ) -> CredentialVerification:
        """Resolve one identity by email and check its password.

        The email comparison is case-insensitive because mailbox-local casing is
        not something a person reliably reproduces when signing in. Omitting the
        organization is supported for single-tenant deployments, where asking a
        person to type a tenant UUID to sign in is not reasonable; an email that
        matches more than one tenant is rejected rather than guessed.
        """

        statement = select(User).where(
            User.email.ilike(email.strip()),
            User.active.is_(True),
        )
        if organization_id is not None:
            statement = statement.where(User.organization_id == organization_id)
        candidates = (await session.scalars(statement.limit(2))).all()
        if len(candidates) != 1:
            return await self._equivalent_rejection(plaintext)
        user = candidates[0]
        credential = await self._load(
            session, organization_id=user.organization_id, user_id=user.id, for_update=True
        )
        if credential is None:
            return await self._equivalent_rejection(plaintext)

        now = utc_now()
        if credential.locked_until is not None and ensure_utc(credential.locked_until) > now:
            return CredentialVerification(
                CredentialOutcome.LOCKED,
                locked_until=ensure_utc(credential.locked_until),
            )

        matched = await asyncio.to_thread(verify_password, plaintext, credential.encoded_secret)
        if not matched:
            credential.failed_attempts += 1
            credential.updated_at = now
            if credential.failed_attempts >= self.max_failed_attempts:
                credential.locked_until = now + timedelta(seconds=self.lockout_seconds)
                await session.flush()
                return CredentialVerification(
                    CredentialOutcome.LOCKED,
                    locked_until=credential.locked_until,
                )
            await session.flush()
            return CredentialVerification(CredentialOutcome.REJECTED)

        credential.failed_attempts = 0
        credential.locked_until = None
        credential.last_verified_at = now
        credential.updated_at = now
        # An accepted password is the only moment the plaintext is available, so
        # it is also the only moment a cost-parameter upgrade can be applied.
        if needs_rehash(credential.encoded_secret, parameters=self.parameters):
            credential.encoded_secret = await self._hash(plaintext)
            credential.algorithm = ALGORITHM
            credential.rotated_at = now
        await session.flush()
        return CredentialVerification(
            CredentialOutcome.VERIFIED,
            user_id=user.id,
            organization_id=user.organization_id,
            must_change=credential.must_change,
        )

    async def _hash(self, plaintext: str) -> str:
        # scrypt is deliberately expensive; deriving on the event loop would
        # stall every other request served by the same worker.
        return await asyncio.to_thread(hash_password, plaintext, parameters=self.parameters)

    async def _equivalent_rejection(self, plaintext: str) -> CredentialVerification:
        # Keep an absent or ambiguous identity on the same KDF path as a wrong
        # password. Response fields alone are not enough to prevent enumeration.
        await asyncio.to_thread(
            verify_password,
            plaintext,
            verification_sentinel(parameters=self.parameters),
        )
        return CredentialVerification(CredentialOutcome.REJECTED)

    async def _load(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> UserCredential | None:
        statement = select(UserCredential).where(
            UserCredential.organization_id == organization_id,
            UserCredential.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(UserCredential | None, await session.scalar(statement))
