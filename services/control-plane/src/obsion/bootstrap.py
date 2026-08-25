from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.config import AuthMode, Settings
from obsion.db.models import Organization, Role, User, UserRole
from obsion.registry.builtins import bootstrap_builtin_registry


async def bootstrap_development_identity(session: AsyncSession, settings: Settings) -> None:
    """Create only the deterministic local identity required by development auth."""

    if settings.auth_mode != AuthMode.DEVELOPMENT:
        return
    organization = await session.get(Organization, settings.dev_organization_id)
    if organization is None:
        organization = Organization(
            id=settings.dev_organization_id,
            slug="local",
            name="Obsion Local",
            active=True,
            settings={},
        )
        session.add(organization)
        await session.flush()

    external_id = str(settings.dev_user_id)
    user = await session.scalar(
        select(User).where(
            User.organization_id == organization.id,
            User.external_id == external_id,
        )
    )
    if user is None:
        user = User(
            id=settings.dev_user_id,
            organization_id=organization.id,
            external_id=external_id,
            email="local-admin@localhost",
            display_name="Local Administrator",
            department="Engineering",
            active=True,
            attributes={"environment": "development"},
        )
        session.add(user)
        await session.flush()

    role = await session.scalar(
        select(Role).where(
            Role.organization_id == organization.id,
            Role.name == "Organization Administrator",
        )
    )
    if role is None:
        role = Role(
            organization_id=organization.id,
            name="Organization Administrator",
            description="Local development administrator",
            permissions=["*"],
            system=True,
        )
        session.add(role)
        await session.flush()

    binding = await session.scalar(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
    )
    if binding is None:
        session.add(
            UserRole(
                organization_id=organization.id,
                user_id=user.id,
                role_id=role.id,
                scope={},
            )
        )

    await bootstrap_builtin_registry(session, organization.id, user.id)
