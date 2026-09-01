"""Phase 93: native Anthropic and Gemini model adapters.

Unit tests pin both adapters' wire contracts (request shape, credential
headers, tool_choice mapping, response parsing, protocol errors) and
gateway-level tests prove a profile can route to anthropic or gemini
endpoints through the same ModelGateway path — redaction, budgets, tool
validation, and cost accounting unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from test_phase6_model_gateway import ORGANIZATION_ID, RUN_ID, _database, _seed_profile

from obsion.db.models import Organization
from obsion.domain.enums import Classification
from obsion.model_gateway.gateway import ModelGateway
from obsion.model_gateway.providers import (
    ANTHROPIC_PROVIDERS,
    GEMINI_PROVIDERS,
    OPENAI_COMPATIBLE_PROVIDERS,
    SUPPORTED_PROVIDERS,
    AnthropicAdapter,
    GeminiAdapter,
    ModelTool,
    ProviderCompletionRequest,
    ProviderProtocolError,
    builtin_provider_adapters,
)
from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]

MESSAGES = [
    {"role": "system", "content": "You are an incident analyst."},
    {"role": "user", "content": "Summarize the outage."},
]
TOOLS = (
    ModelTool(
        name="metric_query",
        description="Query a metric series",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
)


def _request(**overrides) -> ProviderCompletionRequest:
    values = {
        "model_id": "claude-sonnet-4-5",
        "messages": MESSAGES,
        "temperature": 0.2,
        "max_output_tokens": 1024,
        "json_mode": False,
        "tools": (),
        "tool_choice": None,
    }
    values.update(overrides)
    return ProviderCompletionRequest(**values)


def _response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def test_supported_providers_register_native_adapters() -> None:
    adapters = builtin_provider_adapters()
    assert OPENAI_COMPATIBLE_PROVIDERS | ANTHROPIC_PROVIDERS | GEMINI_PROVIDERS == (
        SUPPORTED_PROVIDERS
    )
    assert set(adapters) == set(SUPPORTED_PROVIDERS)
    assert isinstance(adapters["anthropic"], AnthropicAdapter)
    assert isinstance(adapters["gemini"], GeminiAdapter)


def test_anthropic_request_maps_system_tools_and_choice() -> None:
    built = AnthropicAdapter().build_completion_request(
        _request(tools=TOOLS, tool_choice="metric_query"),
        credential="test-credential",
    )
    assert built.path == "v1/messages"
    assert built.headers["x-api-key"] == "test-credential"
    assert built.headers["anthropic-version"] == "2023-06-01"
    payload = built.payload
    assert payload["model"] == "claude-sonnet-4-5"
    assert payload["max_tokens"] == 1024
    assert payload["system"] == "You are an incident analyst."
    assert payload["messages"] == [{"role": "user", "content": "Summarize the outage."}]
    assert payload["tools"] == [
        {
            "name": "metric_query",
            "description": "Query a metric series",
            "input_schema": TOOLS[0].input_schema,
        }
    ]
    assert payload["tool_choice"] == {"type": "tool", "name": "metric_query"}


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("auto", {"type": "auto"}),
        ("required", {"type": "any"}),
        ("none", {"type": "none"}),
    ],
)
def test_anthropic_tool_choice_modes(choice: str, expected: dict) -> None:
    built = AnthropicAdapter().build_completion_request(
        _request(tools=TOOLS, tool_choice=choice),
        credential=None,
    )
    assert "x-api-key" not in built.headers
    assert built.payload["tool_choice"] == expected


def test_anthropic_json_mode_appends_instruction_to_system() -> None:
    built = AnthropicAdapter().build_completion_request(_request(json_mode=True), credential=None)
    assert "JSON object" in built.payload["system"]
    assert "incident analyst" in built.payload["system"]


def test_anthropic_parse_text_tool_use_and_usage() -> None:
    completion = AnthropicAdapter().parse_completion_response(
        _response(
            {
                "content": [
                    {"type": "text", "text": "The outage "},
                    {"type": "text", "text": "lasted 12 minutes."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "metric_query",
                        "input": {"name": "errors"},
                    },
                ],
                "usage": {"input_tokens": 11, "output_tokens": 7},
                "stop_reason": "tool_use",
            }
        )
    )
    assert completion.content == "The outage lasted 12 minutes."
    assert completion.tool_calls[0].id == "toolu_1"
    assert completion.tool_calls[0].name == "metric_query"
    assert completion.tool_calls[0].arguments == {"name": "errors"}
    assert (completion.input_tokens, completion.output_tokens) == (11, 7)
    assert completion.finish_reason == "tool_use"


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "not-a-list"},
        {"content": [{"type": "text", "text": 42}]},
        {"content": [{"type": "tool_use", "id": "x", "name": "metric_query", "input": "raw"}]},
        {"content": [{"type": "image", "source": {}}]},
        {"content": [], "usage": {"input_tokens": -1}},
    ],
)
def test_anthropic_parse_rejects_protocol_violations(payload: dict) -> None:
    with pytest.raises(ProviderProtocolError):
        AnthropicAdapter().parse_completion_response(_response(payload))


def test_gemini_request_maps_roles_config_and_tools() -> None:
    built = GeminiAdapter().build_completion_request(
        _request(
            model_id="gemini-2.5-pro",
            messages=MESSAGES + [{"role": "assistant", "content": "Working on it."}],
            tools=TOOLS,
            tool_choice="metric_query",
            json_mode=True,
        ),
        credential="gemini-credential",
    )
    assert built.path == "v1beta/models/gemini-2.5-pro:generateContent"
    assert built.headers["x-goog-api-key"] == "gemini-credential"
    payload = built.payload
    assert payload["systemInstruction"] == {"parts": [{"text": "You are an incident analyst."}]}
    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "Summarize the outage."}]},
        {"role": "model", "parts": [{"text": "Working on it."}]},
    ]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["maxOutputTokens"] == 1024
    declarations = payload["tools"][0]["functionDeclarations"]
    assert declarations[0]["name"] == "metric_query"
    assert declarations[0]["parameters"] == TOOLS[0].input_schema
    assert payload["toolConfig"] == {
        "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["metric_query"]}
    }


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (None, {"mode": "AUTO"}),
        ("auto", {"mode": "AUTO"}),
        ("required", {"mode": "ANY"}),
        ("none", {"mode": "NONE"}),
    ],
)
def test_gemini_calling_modes(choice: str | None, expected: dict) -> None:
    built = GeminiAdapter().build_completion_request(
        _request(tools=TOOLS, tool_choice=choice),
        credential=None,
    )
    assert built.payload["toolConfig"]["functionCallingConfig"] == expected


def test_gemini_parse_text_function_calls_and_usage() -> None:
    completion = GeminiAdapter().parse_completion_response(
        _response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Checking the metric."},
                                {
                                    "functionCall": {
                                        "name": "metric_query",
                                        "args": {"name": "errors"},
                                    }
                                },
                                {
                                    "functionCall": {
                                        "name": "metric_query",
                                        "args": {"name": "latency"},
                                    }
                                },
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 5},
            }
        )
    )
    assert completion.content == "Checking the metric."
    assert [call.id for call in completion.tool_calls] == ["call_0", "call_1"]
    assert completion.tool_calls[0].arguments == {"name": "errors"}
    assert (completion.input_tokens, completion.output_tokens) == (9, 5)
    assert completion.finish_reason == "STOP"


@pytest.mark.parametrize(
    "payload",
    [
        {"candidates": []},
        {"candidates": [{"content": {"parts": "oops"}}]},
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "bad name!"}}]}}]},
        {"candidates": [{"content": {"parts": [{"thought": True}]}}]},
        {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": True},
        },
    ],
)
def test_gemini_parse_rejects_protocol_violations(payload: dict) -> None:
    with pytest.raises(ProviderProtocolError):
        GeminiAdapter().parse_completion_response(_response(payload))


async def _run_gateway(
    tmp_path: Path,
    name: str,
    provider: str,
    base_url: str,
    responder,
    *,
    tools: tuple[ModelTool, ...] = (),
    tool_choice: str | None = None,
    credential_ref: str | None = None,
):
    settings, database = await _database(tmp_path, name)
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responder(request)

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug=f"phase93-{provider}",
                    name="Phase 93",
                    active=True,
                    settings={},
                )
            )
            profile, endpoint = await _seed_profile(
                session,
                name=f"{provider}-profile",
                endpoint_name=f"{provider}-endpoint",
                provider=provider,
                base_url=base_url,
                capabilities=["chat", "tool_call"],
                requirements={"capabilities": ["chat", "tool_call"]},
            )
            if credential_ref is not None:
                endpoint.credential_ref = credential_ref
            gateway = ModelGateway(settings, transport=httpx.MockTransport(transport))
            result = await gateway.complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=profile.id,
                messages=MESSAGES,
                classification=Classification.INTERNAL,
                tools=tools,
                tool_choice=tool_choice,
            )
            return result, requests
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_gateway_routes_anthropic_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OBSION_PHASE93_MODEL_TOKEN", "phase93-anthropic-token")

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "phase93-anthropic-token"
        payload = json.loads(request.content)
        assert payload["system"] == "You are an incident analyst."
        assert payload["messages"] == [{"role": "user", "content": "Summarize the outage."}]
        assert payload["tool_choice"] == {"type": "tool", "name": "metric_query"}
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_42",
                        "name": "metric_query",
                        "input": {"name": "errors"},
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 9},
                "stop_reason": "tool_use",
            },
        )

    result, requests = await _run_gateway(
        tmp_path,
        "anthropic-e2e.db",
        "anthropic",
        "http://localhost:9999",
        responder,
        tools=TOOLS,
        tool_choice="metric_query",
        credential_ref="env://OBSION_PHASE93_MODEL_TOKEN",
    )
    assert len(requests) == 1
    assert result.tool_calls[0].name == "metric_query"
    assert result.input_tokens == 20
    assert result.output_tokens == 9


@pytest.mark.asyncio
async def test_gateway_routes_gemini_end_to_end(tmp_path: Path) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":generateContent")
        payload = json.loads(request.content)
        assert payload["contents"][0]["role"] == "user"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Outage window 10:04-10:16."}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 14, "candidatesTokenCount": 6},
            },
        )

    result, requests = await _run_gateway(
        tmp_path,
        "gemini-e2e.db",
        "gemini",
        "http://localhost:9998",
        responder,
    )
    assert len(requests) == 1
    assert result.content == "Outage window 10:04-10:16."
    assert result.tool_calls == ()
    assert result.input_tokens == 14


def test_admin_provider_validation_uses_supported_providers() -> None:
    admin = (ROOT / "services/control-plane/src/obsion/api/admin.py").read_text(encoding="utf-8")
    assert "SUPPORTED_PROVIDERS" in admin
    assert "OPENAI_COMPATIBLE_PROVIDERS" not in admin


def test_release_notes_and_project_status_track_phase93() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.93.0-dev.yaml", ROOT)
    assert result["version"] == "0.93.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.94.0-dev"
    assert status["current_phase"] == "phase-94"
    assert "phase-93" in status["completed_phases"]
