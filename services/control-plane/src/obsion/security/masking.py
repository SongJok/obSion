import hashlib
from typing import Any


def _replace_path(value: Any, path: list[str], replacement: Any) -> None:
    if not path or not isinstance(value, dict):
        return
    current = value
    for part in path[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            return
        current = nested
    if path[-1] in current:
        current[path[-1]] = replacement


def apply_obligations(
    payload: dict[str, Any], obligations: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    result = _copy(payload)
    for obligation in obligations:
        kind = obligation.get("type")
        if kind == "mask_fields":
            for path in obligation.get("fields", []):
                _replace_path(result, str(path).split("."), "***")
        elif kind == "hash_fields":
            for path in obligation.get("fields", []):
                _hash_path(result, str(path).split("."))
        elif kind == "limit_result_rows":
            rows = result.get("rows")
            if isinstance(rows, list):
                result["rows"] = rows[: int(obligation.get("value", 500))]
        elif kind == "mask_classified_fields":
            _mask_annotated(result)
    if not isinstance(result, dict):
        return {}
    return result


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def _hash_path(value: dict[str, Any], path: list[str]) -> None:
    current = value
    for part in path[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            return
        current = nested
    field = path[-1]
    if field in current:
        current[field] = hashlib.sha256(str(current[field]).encode()).hexdigest()[:16]


def _mask_annotated(value: Any) -> None:
    if isinstance(value, dict):
        classified = value.get("_classified_fields")
        if isinstance(classified, list):
            for key in classified:
                if key in value:
                    value[key] = "***"
        for item in value.values():
            _mask_annotated(item)
    elif isinstance(value, list):
        for item in value:
            _mask_annotated(item)
