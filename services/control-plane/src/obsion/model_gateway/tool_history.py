"""验证提供商中立的工具历史；工具结果不能升级为系统指令。"""

from __future__ import annotations

import json
from typing import Any

from obsion.model_gateway.providers import (
    ModelToolCall,
    OpenAICompatibleAdapter,
    ProviderProtocolError,
)
from obsion.security.redaction import redact


def assistant_tool_message(
    calls: tuple[ModelToolCall, ...], *, content: str = ""
) -> dict[str, Any]:
    """把尚未执行的模型提议编码为现有 chat-completions 历史契约。"""
    if not calls:
        raise ProviderProtocolError("assistant tool message requires tool calls")
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, allow_nan=False),
                },
            }
            for call in calls
        ],
    }


def tool_result_message(call_id: str, content: str) -> dict[str, Any]:
    """结果保持纯数据，由调用 ID 关联，不能自行指定消息角色。"""
    if not isinstance(call_id, str) or not call_id:
        raise ProviderProtocolError("tool result requires a call id")
    if not isinstance(content, str):
        raise ProviderProtocolError("tool result content must be a string")
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def validate_tool_history(messages: list[dict[str, Any]]) -> None:
    """拒绝孤立、重复或未完成的工具结果，允许不同轮次复用厂商调用 ID。"""
    pending: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ProviderProtocolError("messages must be objects")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or role not in {"system", "user", "assistant", "tool"}:
            raise ProviderProtocolError("unsupported message role")
        allowed_keys = {"role", "content"}
        if role == "assistant":
            allowed_keys.add("tool_calls")
        elif role == "tool":
            allowed_keys.add("tool_call_id")
        if message.keys() - allowed_keys:
            raise ProviderProtocolError("message contains unsupported protocol fields")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ProviderProtocolError(
                    "tool result must reference a pending call exactly once"
                )
            if not isinstance(content, str):
                raise ProviderProtocolError("tool result content must be a string")
            if "tool_calls" in message:
                raise ProviderProtocolError("tool results cannot declare tool calls")
            pending.remove(call_id)
            continue
        if pending:
            raise ProviderProtocolError(
                "all tool results must precede the next conversational turn"
            )
        raw_calls = message.get("tool_calls")
        if "tool_calls" in message:
            if role != "assistant" or not isinstance(raw_calls, list) or not raw_calls:
                raise ProviderProtocolError(
                    "only assistant messages may declare non-empty tool calls"
                )
            if content is not None and not isinstance(content, str):
                raise ProviderProtocolError("assistant content must be a string or null")
            for raw_call in raw_calls:
                call = OpenAICompatibleAdapter._parse_tool_call(raw_call)
                if set(raw_call) != {"id", "type", "function"} or set(raw_call["function"]) != {
                    "name",
                    "arguments",
                }:
                    raise ProviderProtocolError("tool call contains unsupported protocol fields")
                if call.id in pending:
                    raise ProviderProtocolError("tool call ids must be unique within a turn")
                pending.add(call.id)
        elif not isinstance(content, str):
            raise ProviderProtocolError("message content must be a string")
        if "tool_call_id" in message:
            raise ProviderProtocolError("only tool results may reference a call id")
    if pending:
        raise ProviderProtocolError("tool history has unresolved calls")


def _redact_content(content: str) -> str:
    try:
        parsed = json.loads(content)
    except (ValueError, RecursionError):
        return str(redact(content))
    if not isinstance(parsed, (dict, list)):
        return str(redact(content))
    try:
        return json.dumps(redact(parsed), ensure_ascii=False, allow_nan=False)
    except (ValueError, RecursionError) as exc:
        raise ProviderProtocolError("message JSON content must be finite and bounded") from exc


def redact_tool_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """先解码工具 JSON 再脱敏；不改变角色、关联 ID 或调用者的历史。"""
    validate_tool_history(messages)
    safe: list[dict[str, Any]] = []
    for message in messages:
        sanitized = dict(message)
        content = message.get("content")
        if isinstance(content, str):
            sanitized["content"] = _redact_content(content)
        if message.get("tool_calls"):
            calls = []
            for raw_call in message["tool_calls"]:
                call = OpenAICompatibleAdapter._parse_tool_call(raw_call)
                calls.append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(
                                redact(call.arguments), ensure_ascii=False, allow_nan=False
                            ),
                        },
                    }
                )
            sanitized["tool_calls"] = calls
        safe.append(sanitized)
    validate_tool_history(safe)
    return safe
