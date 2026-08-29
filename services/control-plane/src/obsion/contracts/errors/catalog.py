from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_CONTRACT_ROOT = Path(__file__).resolve().parent


class ErrorContractDefinitionError(RuntimeError):
    """机器可读错误码目录自身无效。"""


@dataclass(frozen=True, slots=True)
class ErrorCodeDefinition:
    code: str
    category: str
    http_status: int | None
    description: str


def get_error_code(code: str) -> ErrorCodeDefinition:
    definition = _load_catalog().get(code)
    if definition is None:
        raise ErrorContractDefinitionError(f"Unregistered Obsion error code: {code}")
    return definition


def validate_error_code(code: str | None) -> str | None:
    if code is not None:
        get_error_code(code)
    return code


def validate_error_catalog() -> int:
    return len(_load_catalog())


def registered_error_codes() -> frozenset[str]:
    return frozenset(_load_catalog())


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, ErrorCodeDefinition]:
    schema = _read_json("catalog.schema.json")
    document = _read_json("catalog.json")
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(document)
        records = document["codes"]
        codes = [item["code"] for item in records]
        if codes != sorted(codes) or len(codes) != len(set(codes)):
            raise ValueError("Error codes are not unique and ordered")
        return {
            item["code"]: ErrorCodeDefinition(
                code=item["code"],
                category=item["category"],
                http_status=item["http_status"],
                description=item["description"],
            )
            for item in records
        }
    except Exception as exc:
        raise ErrorContractDefinitionError("Error catalog is not a valid frozen contract") from exc


def _read_json(name: str) -> dict[str, Any]:
    try:
        document = json.loads((_CONTRACT_ROOT / name).read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorContractDefinitionError(f"Cannot load error contract: {name}") from exc
    if not isinstance(document, dict):
        raise ErrorContractDefinitionError(f"Error contract must be a JSON object: {name}")
    return document
