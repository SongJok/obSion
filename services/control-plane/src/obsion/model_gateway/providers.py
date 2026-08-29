from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class ModelTool:
    """Provider-neutral tool declaration exposed by the Model Gateway."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    """A normalized, unexecuted tool request returned by a model provider."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderCompletionRequest:
    model_id: str
    messages: list[dict[str, Any]]
    temperature: float
    max_output_tokens: int
    json_mode: bool
    tools: tuple[ModelTool, ...]
    tool_choice: str | None


@dataclass(frozen=True, slots=True)
class ProviderHTTPRequest:
    path: str
    headers: dict[str, str]
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderCompletion:
    content: str
    tool_calls: tuple[ModelToolCall, ...]
    input_tokens: int
    output_tokens: int
    finish_reason: str | None


class ProviderProtocolError(ValueError):
    """The provider returned a response outside its declared protocol."""


class ModelProviderAdapter(Protocol):
    """Vendor protocol boundary; the Harness never imports or implements this API."""

    def build_completion_request(
        self,
        request: ProviderCompletionRequest,
        *,
        credential: str | None,
    ) -> ProviderHTTPRequest: ...

    def parse_completion_response(self, response: httpx.Response) -> ProviderCompletion: ...


class OpenAICompatibleAdapter:
    """Adapter for providers implementing the OpenAI chat-completions wire contract."""

    def build_completion_request(
        self,
        request: ProviderCompletionRequest,
        *,
        credential: str | None,
    ) -> ProviderHTTPRequest:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        payload: dict[str, Any] = {
            "model": request.model_id,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": False,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
            if request.tool_choice in {"auto", "none", "required"}:
                payload["tool_choice"] = request.tool_choice
            elif request.tool_choice is not None:
                payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": request.tool_choice},
                }
        return ProviderHTTPRequest(
            path="chat/completions",
            headers=headers,
            payload=payload,
        )

    def parse_completion_response(self, response: httpx.Response) -> ProviderCompletion:
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            raw_content = message.get("content")
            if raw_content is not None and not isinstance(raw_content, str):
                raise ProviderProtocolError("completion content must be a string or null")
            raw_tool_calls = message.get("tool_calls", [])
            if not isinstance(raw_tool_calls, list):
                raise ProviderProtocolError("tool_calls must be an array")
            tool_calls = tuple(self._parse_tool_call(item) for item in raw_tool_calls)
            usage = body.get("usage", {})
            input_tokens = _nonnegative_int(usage.get("prompt_tokens", 0), "prompt_tokens")
            output_tokens = _nonnegative_int(usage.get("completion_tokens", 0), "completion_tokens")
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ProviderProtocolError("finish_reason must be a string or null")
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError("invalid chat-completions response") from exc
        return ProviderCompletion(
            content=raw_content or "",
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_tool_call(value: Any) -> ModelToolCall:
        if not isinstance(value, dict):
            raise ProviderProtocolError("tool call must be an object")
        call_id = value.get("id")
        function = value.get("function")
        if (
            value.get("type") != "function"
            or not isinstance(call_id, str)
            or not call_id
            or not isinstance(function, dict)
        ):
            raise ProviderProtocolError("tool call id and function are required")
        name = function.get("name")
        raw_arguments = function.get("arguments")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise ProviderProtocolError("tool call name is invalid")
        if not isinstance(raw_arguments, str):
            raise ProviderProtocolError("tool call arguments must be JSON text")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError("tool call arguments are not valid JSON") from exc
        if not isinstance(arguments, dict):
            raise ProviderProtocolError("tool call arguments must decode to an object")
        return ModelToolCall(id=call_id, name=name, arguments=arguments)


OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {
        "openai",
        "openai-compatible",
        "deepseek",
        "qwen",
        "glm",
        "local",
    }
)


def builtin_provider_adapters() -> dict[str, ModelProviderAdapter]:
    adapter = OpenAICompatibleAdapter()
    return {provider: adapter for provider in OPENAI_COMPATIBLE_PROVIDERS}


def validate_tools(tools: tuple[ModelTool, ...], tool_choice: str | None) -> None:
    if len(tools) > 128:
        raise ValueError("a completion request may declare at most 128 tools")
    names: set[str] = set()
    for tool in tools:
        if not _TOOL_NAME.fullmatch(tool.name):
            raise ValueError(f"invalid tool name: {tool.name}")
        if tool.name in names:
            raise ValueError(f"duplicate tool name: {tool.name}")
        names.add(tool.name)
        if not tool.description.strip() or len(tool.description) > 4096:
            raise ValueError(f"tool description is invalid: {tool.name}")
        try:
            Draft202012Validator.check_schema(tool.input_schema)
        except SchemaError as exc:
            raise ValueError(f"tool input schema is invalid: {tool.name}") from exc
    if tool_choice is not None:
        if not tools:
            raise ValueError("tool_choice requires at least one tool")
        if tool_choice not in {"auto", "none", "required"} and tool_choice not in names:
            raise ValueError("tool_choice must be auto, none, required, or a declared tool name")


def validate_tool_calls(
    calls: tuple[ModelToolCall, ...],
    tools: tuple[ModelTool, ...],
    tool_choice: str | None,
) -> None:
    declarations = {tool.name: tool for tool in tools}
    seen_ids: set[str] = set()
    for call in calls:
        if call.id in seen_ids:
            raise ProviderProtocolError("tool call ids must be unique")
        seen_ids.add(call.id)
        declaration = declarations.get(call.name)
        if declaration is None:
            raise ProviderProtocolError("provider requested an undeclared tool")
        errors = sorted(
            Draft202012Validator(declaration.input_schema).iter_errors(call.arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise ProviderProtocolError("tool call arguments do not match the declared schema")
    if tool_choice == "none" and calls:
        raise ProviderProtocolError("provider returned a tool call when tool_choice was none")
    if tool_choice == "required" and not calls:
        raise ProviderProtocolError("provider omitted a required tool call")
    if tool_choice not in {None, "auto", "none", "required"} and (
        not calls or any(call.name != tool_choice for call in calls)
    ):
        raise ProviderProtocolError("provider did not honor the selected tool")


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ProviderProtocolError(f"{field} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderProtocolError(f"{field} must be a non-negative integer") from exc
    if result < 0:
        raise ProviderProtocolError(f"{field} must be a non-negative integer")
    return result
