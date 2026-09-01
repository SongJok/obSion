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

ANTHROPIC_PROVIDERS = frozenset({"anthropic"})
GEMINI_PROVIDERS = frozenset({"gemini"})
SUPPORTED_PROVIDERS = OPENAI_COMPATIBLE_PROVIDERS | ANTHROPIC_PROVIDERS | GEMINI_PROVIDERS

_ANTHROPIC_VERSION = "2023-06-01"
_JSON_MODE_INSTRUCTION = (
    "Respond with exactly one valid JSON object and no other text, markdown fences, or commentary."
)


def _split_system_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Separates system-role content from the conversational messages.

    The Harness renders system, user, and assistant string messages;
    Anthropic and Gemini both lift system content out of the message list.
    """

    system_parts: list[str] = []
    conversation: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ProviderProtocolError("messages must be objects")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderProtocolError("message content must be a string")
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            conversation.append({"role": role, "content": content})
        else:
            raise ProviderProtocolError(f"unsupported message role: {role!r}")
    return "\n\n".join(system_parts), conversation


class AnthropicAdapter:
    """Adapter for the Anthropic Messages API (Claude models)."""

    def build_completion_request(
        self,
        request: ProviderCompletionRequest,
        *,
        credential: str | None,
    ) -> ProviderHTTPRequest:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        if credential:
            headers["x-api-key"] = credential
        system, conversation = _split_system_messages(request.messages)
        if request.json_mode:
            system = f"{system}\n\n{_JSON_MODE_INSTRUCTION}" if system else _JSON_MODE_INSTRUCTION
        payload: dict[str, Any] = {
            "model": request.model_id,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "messages": [
                {"role": message["role"], "content": message["content"]} for message in conversation
            ],
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
            if request.tool_choice == "auto":
                payload["tool_choice"] = {"type": "auto"}
            elif request.tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
            elif request.tool_choice == "none":
                payload["tool_choice"] = {"type": "none"}
            elif request.tool_choice is not None:
                payload["tool_choice"] = {"type": "tool", "name": request.tool_choice}
        return ProviderHTTPRequest(path="v1/messages", headers=headers, payload=payload)

    def parse_completion_response(self, response: httpx.Response) -> ProviderCompletion:
        try:
            body = response.json()
            blocks = body["content"]
            if not isinstance(blocks, list):
                raise ProviderProtocolError("content must be an array of blocks")
            text_parts: list[str] = []
            tool_calls: list[ModelToolCall] = []
            for block in blocks:
                if not isinstance(block, dict):
                    raise ProviderProtocolError("content block must be an object")
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text")
                    if not isinstance(text, str):
                        raise ProviderProtocolError("text block content must be a string")
                    text_parts.append(text)
                elif block_type == "tool_use":
                    tool_calls.append(self._parse_tool_use(block))
                else:
                    raise ProviderProtocolError(f"unsupported content block type: {block_type!r}")
            usage = body.get("usage", {})
            input_tokens = _nonnegative_int(usage.get("input_tokens", 0), "input_tokens")
            output_tokens = _nonnegative_int(usage.get("output_tokens", 0), "output_tokens")
            finish_reason = body.get("stop_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ProviderProtocolError("stop_reason must be a string or null")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError("invalid Anthropic messages response") from exc
        return ProviderCompletion(
            content="".join(text_parts),
            tool_calls=tuple(tool_calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_tool_use(block: dict[str, Any]) -> ModelToolCall:
        call_id = block.get("id")
        name = block.get("name")
        arguments = block.get("input")
        if not isinstance(call_id, str) or not call_id:
            raise ProviderProtocolError("tool_use block id is required")
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise ProviderProtocolError("tool_use block name is invalid")
        if not isinstance(arguments, dict):
            raise ProviderProtocolError("tool_use block input must be an object")
        return ModelToolCall(id=call_id, name=name, arguments=arguments)


class GeminiAdapter:
    """Adapter for the Gemini generateContent API."""

    def build_completion_request(
        self,
        request: ProviderCompletionRequest,
        *,
        credential: str | None,
    ) -> ProviderHTTPRequest:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if credential:
            headers["x-goog-api-key"] = credential
        system, conversation = _split_system_messages(request.messages)
        generation_config: dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.json_mode:
            generation_config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user" if message["role"] == "user" else "model",
                    "parts": [{"text": message["content"]}],
                }
                for message in conversation
            ],
            "generationConfig": generation_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        }
                        for tool in request.tools
                    ]
                }
            ]
            calling_config: dict[str, Any]
            if request.tool_choice == "required":
                calling_config = {"mode": "ANY"}
            elif request.tool_choice == "none":
                calling_config = {"mode": "NONE"}
            elif request.tool_choice is None or request.tool_choice == "auto":
                calling_config = {"mode": "AUTO"}
            else:
                calling_config = {
                    "mode": "ANY",
                    "allowedFunctionNames": [request.tool_choice],
                }
            payload["toolConfig"] = {"functionCallingConfig": calling_config}
        return ProviderHTTPRequest(
            path=f"v1beta/models/{request.model_id}:generateContent",
            headers=headers,
            payload=payload,
        )

    def parse_completion_response(self, response: httpx.Response) -> ProviderCompletion:
        try:
            body = response.json()
            candidates = body["candidates"]
            if not isinstance(candidates, list) or not candidates:
                raise ProviderProtocolError("candidates must be a non-empty array")
            candidate = candidates[0]
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list):
                raise ProviderProtocolError("content parts must be an array")
            text_parts: list[str] = []
            tool_calls: list[ModelToolCall] = []
            for part in parts:
                if not isinstance(part, dict):
                    raise ProviderProtocolError("content part must be an object")
                if "text" in part:
                    text = part["text"]
                    if not isinstance(text, str):
                        raise ProviderProtocolError("text part content must be a string")
                    text_parts.append(text)
                elif "functionCall" in part:
                    tool_calls.append(
                        self._parse_function_call(part["functionCall"], len(tool_calls))
                    )
                else:
                    raise ProviderProtocolError("content part must be text or functionCall")
            usage = body.get("usageMetadata", {})
            input_tokens = _nonnegative_int(usage.get("promptTokenCount", 0), "promptTokenCount")
            output_tokens = _nonnegative_int(
                usage.get("candidatesTokenCount", 0), "candidatesTokenCount"
            )
            finish_reason = candidate.get("finishReason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ProviderProtocolError("finishReason must be a string or null")
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError("invalid Gemini generateContent response") from exc
        return ProviderCompletion(
            content="".join(text_parts),
            tool_calls=tuple(tool_calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_function_call(value: Any, ordinal: int) -> ModelToolCall:
        if not isinstance(value, dict):
            raise ProviderProtocolError("functionCall must be an object")
        name = value.get("name")
        arguments = value.get("args", {})
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise ProviderProtocolError("functionCall name is invalid")
        if not isinstance(arguments, dict):
            raise ProviderProtocolError("functionCall args must be an object")
        # Gemini does not assign call ids; the ordinal keeps ids unique per completion.
        return ModelToolCall(id=f"call_{ordinal}", name=name, arguments=arguments)


def builtin_provider_adapters() -> dict[str, ModelProviderAdapter]:
    adapters: dict[str, ModelProviderAdapter] = {
        provider: OpenAICompatibleAdapter() for provider in OPENAI_COMPATIBLE_PROVIDERS
    }
    anthropic = AnthropicAdapter()
    adapters.update({provider: anthropic for provider in ANTHROPIC_PROVIDERS})
    gemini = GeminiAdapter()
    adapters.update({provider: gemini for provider in GEMINI_PROVIDERS})
    return adapters


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
