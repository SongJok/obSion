"""Operator-driven provisioning of a password-authenticated identity.

This is the path an operator uses to create the first administrator, before any
identity provider exists and therefore before anyone can sign in to use the
admin API. It is deliberately reachable only from the CLI: it grants a role
without an authenticated caller, so it must never be exposed over HTTP.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError, ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Organization, Role, User, UserRole
from obsion.domain.enums import SystemRole
from obsion.security.auth import credential_store, password_policy
from obsion.security.passwords import PasswordPolicy
from obsion.security.roles import SYSTEM_ROLE_DEFINITIONS

_WEAK_PASSWORD_FLOOR = 6


@dataclass(frozen=True, slots=True)
class ProvisionedIdentity:
    organization_id: UUID
    organization_slug: str
    user_id: UUID
    email: str
    display_name: str
    roles: tuple[str, ...]
    created: bool


async def provision_password_identity(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    role: SystemRole,
    display_name: str | None = None,
    organization: str | None = None,
    allow_weak_password: bool = False,
    must_change: bool = False,
) -> ProvisionedIdentity:
    """Create or update one identity, grant it a system role, and set its password.

    An existing identity keeps its `external_id` so a person who was already
    provisioned by an identity provider does not lose their federated mapping
    when a local password is added.
    """

    normalized_email = email.strip()
    if not normalized_email or "@" not in normalized_email:
        raise ValidationError(
            "identity_email_invalid", "A provisioned identity requires an email address"
        )

    policy = _resolve_policy(settings, allow_weak_password=allow_weak_password)
    organization_row = await _resolve_organization(session, settings, organization)

    users = (
        await session.scalars(
            select(User)
            .where(
                User.organization_id == organization_row.id,
                User.email.ilike(normalized_email),
            )
            .limit(2)
        )
    ).all()
    if len(users) > 1:
        raise ValidationError(
            "identity_email_ambiguous",
            "More than one identity has this email in the selected organization",
        )
    user = users[0] if users else None
    created = user is None
    if user is None:
        user = User(
            organization_id=organization_row.id,
            external_id=normalized_email,
            email=normalized_email,
            display_name=display_name or normalized_email.partition("@")[0],
            active=True,
            attributes={},
        )
        session.add(user)
        await session.flush()
    else:
        user.email = normalized_email
        user.active = True
        if display_name:
            user.display_name = display_name

    granted = await _grant_role(session, organization_row.id, user.id, role)
    await credential_store(settings).enroll(
        session,
        organization_id=organization_row.id,
        user_id=user.id,
        plaintext=password,
        policy=policy,
        must_change=must_change,
    )
    return ProvisionedIdentity(
        organization_id=organization_row.id,
        organization_slug=organization_row.slug,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=granted,
        created=created,
    )


def _resolve_policy(settings: Settings, *, allow_weak_password: bool) -> PasswordPolicy:
    policy = password_policy(settings)
    if not allow_weak_password:
        return policy
    if settings.environment not in {Environment.DEVELOPMENT, Environment.TEST}:
        raise ValidationError(
            "password_policy_violation",
            "Weak passwords may only be provisioned in development or test",
            environment=settings.environment.value,
        )
    return PasswordPolicy(
        min_length=min(_WEAK_PASSWORD_FLOOR, policy.min_length),
        max_length=policy.max_length,
        allow_breached=True,
    )


async def _resolve_organization(
    session: AsyncSession,
    settings: Settings,
    organization: str | None,
) -> Organization:
    if organization is None:
        row = await session.get(Organization, settings.dev_organization_id)
        if row is None:
            raise NotFoundError("Default organization", settings.dev_organization_id)
        return row
    try:
        identifier = UUID(organization)
    except ValueError:
        row = await session.scalar(select(Organization).where(Organization.slug == organization))
    else:
        row = await session.get(Organization, identifier)
    if row is None:
        raise NotFoundError("Organization", organization)
    return row


async def _grant_role(
    session: AsyncSession,
    organization_id: UUID,
    user_id: UUID,
    role: SystemRole,
) -> tuple[str, ...]:
    definition = next(item for item in SYSTEM_ROLE_DEFINITIONS if item.name == role)
    row = await session.scalar(
        select(Role).where(
            Role.organization_id == organization_id,
            Role.name == definition.name.value,
        )
    )
    if row is None:
        row = Role(
            organization_id=organization_id,
            name=definition.name.value,
            description=definition.description,
            permissions=list(definition.permissions),
            system=True,
        )
        session.add(row)
        await session.flush()
    binding = await session.scalar(
        select(UserRole).where(
            UserRole.organization_id == organization_id,
            UserRole.user_id == user_id,
            UserRole.role_id == row.id,
        )
    )
    if binding is None:
        session.add(
            UserRole(
                organization_id=organization_id,
                user_id=user_id,
                role_id=row.id,
                scope={},
            )
        )
        await session.flush()
    bound = await session.execute(
        select(Role.name)
        .join(
            UserRole,
            (UserRole.organization_id == Role.organization_id) & (UserRole.role_id == Role.id),
        )
        .where(UserRole.organization_id == organization_id, UserRole.user_id == user_id)
    )
    return tuple(sorted(bound.scalars().all()))
