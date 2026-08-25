from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ObsionError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class NotFoundError(ObsionError):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__(
            code="resource_not_found",
            message=f"{resource} was not found",
            status_code=404,
            details={"resource": resource, "id": str(resource_id)},
        )


class ConflictError(ObsionError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code=code, message=message, status_code=409, details=details)


class AuthorizationError(ObsionError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code=code, message=message, status_code=403, details=details)


class ValidationError(ObsionError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(code=code, message=message, status_code=422, details=details)


class BudgetExceededError(ObsionError):
    def __init__(self, budget: str, limit: object) -> None:
        super().__init__(
            code="budget_exceeded",
            message="The governed run budget does not permit this operation",
            status_code=429,
            details={"budget": budget, "limit": str(limit)},
        )
