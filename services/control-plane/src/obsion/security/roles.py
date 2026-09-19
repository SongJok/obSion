from dataclasses import dataclass

from obsion.domain.enums import SystemRole


@dataclass(frozen=True, slots=True)
class SystemRoleDefinition:
    name: SystemRole
    description: str
    permissions: tuple[str, ...]


SYSTEM_ROLE_DEFINITIONS: tuple[SystemRoleDefinition, ...] = (
    SystemRoleDefinition(
        SystemRole.ADMIN,
        "Organization administrator with full control-plane access",
        ("*",),
    ),
    SystemRoleDefinition(
        SystemRole.ENGINEER,
        "Engineering contributor for governed investigation and development workflows",
        (
            "artifact.write",
            "automation.trigger",
            "catalog.discover",
            "code.read.confidential",
            "code.read.internal",
            "code.write",
            "evaluations.read",
            "evaluations.write",
            "knowledge.read.confidential",
            "knowledge.read.internal",
            "knowledge.write",
            "memory.read",
            "memory.write",
            "registry.read",
            "registry.write",
        ),
    ),
    SystemRoleDefinition(
        SystemRole.ANALYST,
        "Data analyst for governed research, semantic data, and evidence workflows",
        (
            "artifact.write",
            "catalog.discover",
            "evaluations.read",
            "knowledge.read.confidential",
            "knowledge.read.internal",
            "memory.read",
            "memory.write",
        ),
    ),
    SystemRoleDefinition(
        SystemRole.OPERATOR,
        "Operations responder for governed observability and approval workflows",
        (
            "approval.decide",
            "approval.read",
            "artifact.write",
            "automation.trigger",
            "catalog.discover",
            "knowledge.read.confidential",
            "knowledge.read.internal",
            "memory.read",
            "memory.write",
        ),
    ),
    SystemRoleDefinition(
        SystemRole.SUPPORT,
        "Support investigator with bounded internal knowledge and workspace access",
        (
            "artifact.write",
            "catalog.discover",
            "knowledge.read.internal",
            "memory.read",
            "memory.write",
        ),
    ),
    SystemRoleDefinition(
        SystemRole.VIEWER,
        "Read-only participant for authorized workspaces and internal knowledge",
        (
            "catalog.discover",
            "knowledge.read.internal",
            "memory.read",
        ),
    ),
)

SYSTEM_ROLE_NAMES = frozenset(definition.name.value for definition in SYSTEM_ROLE_DEFINITIONS)
