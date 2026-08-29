from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.config import AuthMode, Settings
from obsion.db.models import Department, Organization, Role, User, UserRole
from obsion.domain.enums import SystemRole
from obsion.registry.builtins import bootstrap_builtin_registry
from obsion.security.roles import SYSTEM_ROLE_DEFINITIONS

DEFAULT_DEVELOPMENT_ORGANIZATION_ID = "00000000-0000-7000-8000-000000000001"


async def bootstrap_development_identity(session: AsyncSession, settings: Settings) -> None:
    """Create only the deterministic local identity required by development auth."""

    if settings.auth_mode != AuthMode.DEVELOPMENT:
        return
    organization = await session.get(Organization, settings.dev_organization_id)
    if organization is None:
        organization = Organization(
            id=settings.dev_organization_id,
            slug=(
                "local"
                if str(settings.dev_organization_id) == DEFAULT_DEVELOPMENT_ORGANIZATION_ID
                else f"local-{settings.dev_organization_id.hex}"
            ),
            name="Obsion Local",
            active=True,
            settings={},
        )
        session.add(organization)
        await session.flush()

    department = await session.scalar(
        select(Department).where(
            Department.organization_id == organization.id,
            Department.name == "Engineering",
        )
    )
    if department is None:
        department = Department(
            organization_id=organization.id,
            name="Engineering",
            description="Local development engineering department",
            active=True,
        )
        session.add(department)
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
            department_id=department.id,
            active=True,
            attributes={"environment": "development"},
        )
        session.add(user)
        await session.flush()

    roles: dict[SystemRole, Role] = {}
    for definition in SYSTEM_ROLE_DEFINITIONS:
        role = await session.scalar(
            select(Role).where(
                Role.organization_id == organization.id,
                Role.name == definition.name.value,
            )
        )
        if role is None:
            role = Role(
                organization_id=organization.id,
                name=definition.name.value,
                description=definition.description,
                permissions=list(definition.permissions),
                system=True,
            )
            session.add(role)
            await session.flush()
        else:
            role.description = definition.description
            role.permissions = list(definition.permissions)
            role.system = True
        roles[definition.name] = role

    role = roles[SystemRole.ADMIN]

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
