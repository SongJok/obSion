import json
from dataclasses import dataclass
from typing import Any

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = "2026-08-26"
WEBSOCKET_SUBPROTOCOL = "obsion.jsonrpc.v1"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass(frozen=True, slots=True)
class JsonRpcRequest:
    request_id: str | int | None
    has_id: bool
    method: str
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProtocolFailure(Exception):
    code: int
    message: str
    request_id: str | int | None = None
    data: dict[str, Any] | None = None


def parse_request(raw: str) -> JsonRpcRequest:
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolFailure(PARSE_ERROR, "Parse error") from exc
    if not isinstance(body, dict):
        raise ProtocolFailure(INVALID_REQUEST, "Request must be one JSON object")

    has_id = "id" in body
    request_id = body.get("id")
    if has_id and (
        request_id is None or isinstance(request_id, bool) or not isinstance(request_id, (str, int))
    ):
        raise ProtocolFailure(INVALID_REQUEST, "Request id must be a non-null string or integer")
    if body.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolFailure(INVALID_REQUEST, "jsonrpc must be exactly '2.0'", request_id)
    method = body.get("method")
    if not isinstance(method, str) or not method or len(method) > 120:
        raise ProtocolFailure(INVALID_REQUEST, "method must be a non-empty string", request_id)
    params = body.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolFailure(INVALID_PARAMS, "params must be an object", request_id)
    allowed = {"jsonrpc", "id", "method", "params"}
    if unknown := sorted(set(body) - allowed):
        raise ProtocolFailure(
            INVALID_REQUEST,
            "Request contains unknown fields",
            request_id,
            {"fields": unknown},
        )
    return JsonRpcRequest(
        request_id=request_id,
        has_id=has_id,
        method=method,
        params=params,
    )


def success_response(request_id: str | int, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(
    request_id: str | int | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}
