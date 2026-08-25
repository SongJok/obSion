from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Principal:
    id: UUID
    organization_id: UUID
    external_id: str
    display_name: str
    department: str | None = None
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    attributes: dict[str, Any] = field(default_factory=dict)

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions
