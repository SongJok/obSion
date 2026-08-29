from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from obsion.common.errors import ValidationError
from obsion.db.models import CapabilityDefinition, CapabilityVersion
from obsion.domain.enums import CapabilityTransport, Classification, RiskLevel, SideEffect


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    version_id: str
    name: str
    display_name: str
    description: str
    version: int
    transport: CapabilityTransport
    risk: RiskLevel
    side_effect: SideEffect
    permission: str
    timeout_seconds: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    output: dict[str, Any]
    data_classification: Classification

    @classmethod
    def from_models(
        cls,
        definition: CapabilityDefinition,
        version: CapabilityVersion,
    ) -> "CapabilityDescriptor":
        input_schema = dict(version.input_schema)
        output_schema = dict(version.output_schema)
        _validate_schema(input_schema, "capability_input_invalid")
        _validate_schema(output_schema, "capability_output_invalid")
        evidence_mapping = dict(version.evidence_mapping)
        evidence_type = evidence_mapping.get("type")
        if not isinstance(evidence_type, str) or not evidence_type.strip():
            raise ValidationError(
                "capability_output_invalid",
                "Capability descriptor output must map to an Evidence type",
            )
        return cls(
            id=str(definition.id),
            version_id=str(version.id),
            name=definition.name,
            display_name=definition.display_name,
            description=definition.description,
            version=version.version,
            transport=version.transport,
            risk=version.risk_level,
            side_effect=version.side_effect,
            permission=version.permission_action,
            timeout_seconds=version.timeout_seconds,
            input_schema=input_schema,
            output_schema=output_schema,
            output={"kind": "Evidence", "mapping": evidence_mapping},
            data_classification=version.data_classification,
        )

    def as_view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version_id": self.version_id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "transport": self.transport,
            "risk": self.risk,
            "side_effect": self.side_effect,
            "permission": self.permission,
            "timeout_seconds": self.timeout_seconds,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "output": self.output,
            "data_classification": self.data_classification,
        }


def _validate_schema(schema: dict[str, Any], code: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValidationError(
            code, "Capability descriptor contains an invalid JSON Schema"
        ) from exc
