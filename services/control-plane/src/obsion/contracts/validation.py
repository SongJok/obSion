from dataclasses import dataclass

from obsion.contracts.errors import validate_error_catalog
from obsion.contracts.events import validate_event_contracts


@dataclass(frozen=True, slots=True)
class ContractSummary:
    event_registry_version: int
    event_count: int
    event_version_count: int
    error_code_count: int


def validate_contracts() -> ContractSummary:
    events = validate_event_contracts()
    return ContractSummary(
        event_registry_version=events.registry_version,
        event_count=events.event_count,
        event_version_count=events.version_count,
        error_code_count=validate_error_catalog(),
    )
