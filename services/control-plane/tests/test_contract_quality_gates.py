from __future__ import annotations

import json
from pathlib import Path

from error_producer_manifest import (
    RESERVED_COMPATIBILITY_ERROR_CODES,
    REVIEWED_ERROR_FORWARDING_SINKS,
    REVIEWED_ERROR_HELPER_CALLS,
    REVIEWED_ERROR_ORIGIN_SINKS,
)
from event_producer_manifest import (
    REVIEWED_EVENT_ENUMS,
    REVIEWED_EVENT_HELPER_CALLS,
    REVIEWED_EVENT_SINKS,
)
from static_contract_analysis import analyze_event_producers
from static_error_analysis import analyze_error_producers

from obsion.cli import build_parser
from obsion.contracts.errors import (
    get_error_code,
    registered_error_codes,
    validate_error_catalog,
)
from obsion.contracts.events import validate_event_contracts
from obsion.contracts.events.validation import registered_event_versions
from obsion.main import create_app

_REPOSITORY_ROOT = Path(__file__).parents[3]
_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_OPENAPI_PATH = _REPOSITORY_ROOT / "docs" / "api" / "openapi.json"


def test_openapi_document_is_current() -> None:
    expected = json.loads(_OPENAPI_PATH.read_text(encoding="utf-8"))
    assert create_app().openapi() == expected


def test_contract_cli_is_registered_and_contracts_are_valid() -> None:
    parser = build_parser()
    args = parser.parse_args(["validate-contracts"])
    assert args.command == "validate-contracts"
    events = validate_event_contracts()
    assert events.event_count == events.version_count == 92
    assert validate_error_catalog() == 262


def test_event_payload_error_code_enums_are_registered() -> None:
    catalog_codes = set(registered_error_codes())
    payload_root = _SOURCE_ROOT / "contracts" / "events" / "payloads"
    for path in payload_root.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        error_code_schema = schema.get("properties", {}).get("error_code")
        if not isinstance(error_code_schema, dict):
            continue
        values = error_code_schema.get("enum")
        if values is None:
            continue
        literal_codes = {value for value in values if isinstance(value, str)}
        assert literal_codes <= catalog_codes, path.name


def test_production_event_registry_exactly_covers_reviewed_producers() -> None:
    sources = {
        path.relative_to(_SOURCE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in _SOURCE_ROOT.rglob("*.py")
    }
    analysis = analyze_event_producers(sources)

    assert analysis.sink_pairs == REVIEWED_EVENT_SINKS
    assert analysis.helper_caller_pairs == REVIEWED_EVENT_HELPER_CALLS
    assert analysis.enum_dependencies == REVIEWED_EVENT_ENUMS
    assert analysis.all_event_versions == set(registered_event_versions())
    assert len(analysis.sink_pairs) == 44
    assert len(analysis.helper_caller_pairs) == 56


def test_production_error_catalog_exactly_covers_reviewed_producers() -> None:
    catalog_codes = frozenset(registered_error_codes())
    sources = {
        path.relative_to(_SOURCE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in _SOURCE_ROOT.rglob("*.py")
    }
    analysis = analyze_error_producers(sources, catalog_codes=catalog_codes)

    assert analysis.origin_sinks == REVIEWED_ERROR_ORIGIN_SINKS
    assert analysis.forwarding_sinks == REVIEWED_ERROR_FORWARDING_SINKS
    assert analysis.helper_caller_codes == REVIEWED_ERROR_HELPER_CALLS
    assert analysis.active_origin_codes.isdisjoint(RESERVED_COMPATIBILITY_ERROR_CODES)
    assert analysis.active_origin_codes | RESERVED_COMPATIBILITY_ERROR_CODES == catalog_codes
    assert len(analysis.active_origin_codes) == 260
    assert len(catalog_codes) == 262
    for code in analysis.active_origin_codes:
        assert get_error_code(code).code == code
