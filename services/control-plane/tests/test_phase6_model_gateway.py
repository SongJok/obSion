import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import (
    ModelCall,
    ModelEndpoint,
    ModelProfile,
    ModelProfileEndpoint,
    Organization,
)
from obsion.db.session import Database
from obsion.domain.enums import Classification
from obsion.model_gateway.gateway import ModelGateway, ModelUnavailableError
from obsion.model_gateway.providers import ModelTool

ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000061")
RUN_ID = UUID("00000000-0000-7000-8000-000000000062")


async def _database(tmp_path: Path, name: str) -> tuple[Settings, Database]:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / name}",
    )
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return settings, database


async def _seed_profile(
    session: AsyncSession,
    *,
    name: str,
    endpoint_name: str,
    provider: str = "openai-compatible",
    base_url: str = "http://localhost:9999/v1",
    capabilities: list[str] | None = None,
    requirements: dict | None = None,
    routing_policy: dict | None = None,
    limits: dict | None = None,
    priority: int = 10,
) -> tuple[ModelProfile, ModelEndpoint]:
    profile = ModelProfile(
        organization_id=ORGANIZATION_ID,
        name=name,
        requirements=requirements or {"capabilities": ["chat"]},
        routing_policy=routing_policy or {"fallback": True},
        enabled=True,
    )
    endpoint = ModelEndpoint(
        organization_id=ORGANIZATION_ID,
        name=endpoint_name,
        provider=provider,
        base_url=base_url,
        model_id=f"{endpoint_name}-model",
        classifications=[item.value for item in Classification],
        capabilities=capabilities or ["chat"],
        limits=limits or {"context_window": 32_000, "max_output_tokens": 2_000},
        enabled=True,
    )
    session.add_all([profile, endpoint])
    await session.flush()
    session.add(
        ModelProfileEndpoint(
            profile_id=profile.id,
            endpoint_id=endpoint.id,
            priority=priority,
        )
    )
    await session.flush()
    return profile, endpoint


def _completion(content: str, *, input_tokens: int = 7, output_tokens: int = 3) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
        },
    }


@pytest.mark.asyncio
async def test_switching_profiles_changes_provider_without_harness_changes(tmp_path: Path) -> None:
    settings, database = await _database(tmp_path, "profile-switch.db")
    requested_models: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        return httpx.Response(200, json=_completion(json.dumps({"model": payload["model"]})))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-switch",
                    name="Phase 6 Switch",
                    active=True,
                    settings={},
                )
            )
            fast, fast_endpoint = await _seed_profile(
                session,
                name="fast",
                endpoint_name="fast-openai",
                provider="openai",
            )
            reasoning, reasoning_endpoint = await _seed_profile(
                session,
                name="reasoning-high",
                endpoint_name="reasoning-deepseek",
                provider="deepseek",
            )
            gateway = ModelGateway(settings, transport=httpx.MockTransport(provider))
            fast_result = await gateway.complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=fast.id,
                messages=[{"role": "user", "content": "route this request"}],
                classification=Classification.INTERNAL,
            )
            reasoning_result = await gateway.complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=reasoning.id,
                messages=[{"role": "user", "content": "route this request"}],
                classification=Classification.INTERNAL,
            )

            assert fast_result.profile_id == fast.id
            assert fast_result.endpoint_id == fast_endpoint.id
            assert reasoning_result.profile_id == reasoning.id
            assert reasoning_result.endpoint_id == reasoning_endpoint.id
            assert requested_models == [fast_endpoint.model_id, reasoning_endpoint.model_id]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_model_gateway_redacts_prompt_secrets_before_provider_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, database = await _database(tmp_path, "prompt-redaction.db")
    provider_payloads: list[dict] = []
    provider_authorization: list[str | None] = []
    monkeypatch.setenv("OBSION_PHASE20_MODEL_TOKEN", "transport-only-model-token")

    def provider(request: httpx.Request) -> httpx.Response:
        provider_payloads.append(json.loads(request.content))
        provider_authorization.append(request.headers.get("authorization"))
        return httpx.Response(200, json=_completion(json.dumps({"answer": "safe"})))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase20-model-redaction",
                    name="Phase 20 Model Redaction",
                    active=True,
                    settings={},
                )
            )
            profile, endpoint = await _seed_profile(
                session,
                name="phase20-redaction",
                endpoint_name="phase20-redaction-endpoint",
            )
            endpoint.credential_ref = "env://OBSION_PHASE20_MODEL_TOKEN"
            result = await ModelGateway(
                settings,
                transport=httpx.MockTransport(provider),
            ).complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=profile.id,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "password='production-password-must-not-leave' "
                            "api_key=sk-production-must-not-leave"
                        ),
                    }
                ],
                classification=Classification.INTERNAL,
                json_mode=False,
            )
            assert result.content
        assert provider_payloads
        serialized = json.dumps(provider_payloads[0], ensure_ascii=False)
        assert "production-password-must-not-leave" not in serialized
        assert "sk-production-must-not-leave" not in serialized
        assert "transport-only-model-token" not in serialized
        assert "[REDACTED]" in serialized
        assert provider_authorization == ["Bearer transport-only-model-token"]
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_tool_calls_are_normalized_schema_validated_and_costed(tmp_path: Path) -> None:
    settings, database = await _database(tmp_path, "tool-call.db")

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "knowledge_search",
                    "description": "Search authorized knowledge",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]
        assert payload["tool_choice"] == "required"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_01",
                                    "type": "function",
                                    "function": {
                                        "name": "knowledge_search",
                                        "arguments": json.dumps({"query": "refund policy"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-tools",
                    name="Phase 6 Tools",
                    active=True,
                    settings={},
                )
            )
            profile, endpoint = await _seed_profile(
                session,
                name="reasoning-high",
                endpoint_name="tool-provider",
                capabilities=["chat", "tool_call"],
                limits={
                    "context_window": 32_000,
                    "max_output_tokens": 2_000,
                    "pricing_per_million": {"input": 2, "output": 4},
                },
            )
            tool = ModelTool(
                name="knowledge_search",
                description="Search authorized knowledge",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
            result = await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=profile.id,
                messages=[{"role": "user", "content": "find the refund policy"}],
                classification=Classification.INTERNAL,
                tools=(tool,),
                tool_choice="required",
            )

            assert result.endpoint_id == endpoint.id
            assert result.tool_calls[0].name == "knowledge_search"
            assert result.tool_calls[0].arguments == {"query": "refund policy"}
            assert result.cost_amount == Decimal("0.00004000")
            call = await session.scalar(select(ModelCall))
            assert call is not None
            assert call.operation == "TOOL_CALL"
            assert call.input_tokens == 10
            assert call.output_tokens == 5
            assert call.cost_amount == Decimal("0.00004000")
            assert call.outcome == "SUCCESS"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_json_mode_requires_capability_and_validates_object(tmp_path: Path) -> None:
    settings, database = await _database(tmp_path, "json-mode.db")

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=_completion(json.dumps({"answer": "bounded"})))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-json",
                    name="Phase 6 JSON",
                    active=True,
                    settings={},
                )
            )
            profile, _ = await _seed_profile(
                session,
                name="reasoning-high",
                endpoint_name="json-provider",
                capabilities=["chat", "json_mode"],
            )
            result = await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=profile.id,
                messages=[{"role": "user", "content": "return JSON"}],
                classification=Classification.INTERNAL,
                json_mode=True,
            )
            assert json.loads(result.content) == {"answer": "bounded"}
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_sensitive_classification_forces_private_profile(tmp_path: Path) -> None:
    settings, database = await _database(tmp_path, "private-routing.db")
    requested_models: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        return httpx.Response(200, json=_completion("private answer"))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-private",
                    name="Phase 6 Private",
                    active=True,
                    settings={},
                )
            )
            fast, _ = await _seed_profile(
                session,
                name="fast",
                endpoint_name="public-fast",
            )
            private, private_endpoint = await _seed_profile(
                session,
                name="private",
                endpoint_name="private-local",
                provider="local",
                requirements={"capabilities": ["chat"], "private": True},
                limits={
                    "private": True,
                    "context_window": 32_000,
                    "max_output_tokens": 2_000,
                },
            )
            result = await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=fast.id,
                messages=[{"role": "user", "content": "sensitive context"}],
                classification=Classification.CONFIDENTIAL,
            )

            assert result.profile_id == private.id
            assert result.endpoint_id == private_endpoint.id
            assert requested_models == [private_endpoint.model_id]
            call = await session.scalar(select(ModelCall))
            assert call is not None
            assert call.profile_id == private.id
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_sensitive_classification_fails_closed_without_private_profile(
    tmp_path: Path,
) -> None:
    settings, database = await _database(tmp_path, "private-missing.db")
    provider_called = False

    def provider(_: httpx.Request) -> httpx.Response:
        nonlocal provider_called
        provider_called = True
        return httpx.Response(200, json=_completion("must not be called"))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-private-missing",
                    name="Phase 6 Private Missing",
                    active=True,
                    settings={},
                )
            )
            fast, _ = await _seed_profile(
                session,
                name="fast",
                endpoint_name="public-only",
            )
            with pytest.raises(ModelUnavailableError):
                await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                    session,
                    organization_id=ORGANIZATION_ID,
                    run_id=RUN_ID,
                    step_id=None,
                    profile_id=fast.id,
                    messages=[{"role": "user", "content": "restricted context"}],
                    classification=Classification.RESTRICTED,
                )
            assert not provider_called
            assert await session.scalar(select(ModelCall)) is None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_profile_fallback_audits_failed_and_successful_attempts(tmp_path: Path) -> None:
    settings, database = await _database(tmp_path, "fallback.db")

    def provider(request: httpx.Request) -> httpx.Response:
        if "/primary/" in request.url.path:
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        return httpx.Response(200, json=_completion("fallback answer"))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-fallback",
                    name="Phase 6 Fallback",
                    active=True,
                    settings={},
                )
            )
            profile, primary = await _seed_profile(
                session,
                name="fast",
                endpoint_name="primary",
                base_url="http://localhost:9999/primary/v1",
                routing_policy={"fallback": True},
                priority=10,
            )
            _, secondary = await _seed_profile(
                session,
                name="fast-secondary-binding",
                endpoint_name="secondary",
                base_url="http://localhost:9999/secondary/v1",
                priority=20,
            )
            session.add(
                ModelProfileEndpoint(
                    profile_id=profile.id,
                    endpoint_id=secondary.id,
                    priority=20,
                )
            )
            await session.flush()

            result = await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=profile.id,
                messages=[{"role": "user", "content": "use fallback"}],
                classification=Classification.INTERNAL,
            )

            assert result.endpoint_id == secondary.id
            calls = list(await session.scalars(select(ModelCall).order_by(ModelCall.created_at)))
            assert [(call.endpoint_id, call.outcome) for call in calls] == [
                (primary.id, "FAILED"),
                (secondary.id, "SUCCESS"),
            ]
    finally:
        await database.dispose()


@pytest.mark.parametrize(
    "mutate_response",
    [
        lambda response: response["choices"][0]["message"]["tool_calls"][0]["function"].update(
            {"arguments": json.dumps({"unexpected": True})}
        ),
        lambda response: response["choices"][0]["message"]["tool_calls"][0]["function"].update(
            {"name": "undeclared_tool"}
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_provider_tool_calls_fail_closed(
    tmp_path: Path,
    mutate_response: Callable[[dict], None],
) -> None:
    settings, database = await _database(tmp_path, "invalid-tool-call.db")

    def provider(_: httpx.Request) -> httpx.Response:
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_invalid",
                                "type": "function",
                                "function": {
                                    "name": "knowledge_search",
                                    "arguments": json.dumps({"query": "policy"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }
        mutate_response(response)
        return httpx.Response(200, json=response)

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID,
                    slug="phase6-invalid-tool",
                    name="Phase 6 Invalid Tool",
                    active=True,
                    settings={},
                )
            )
            profile, _ = await _seed_profile(
                session,
                name="reasoning-high",
                endpoint_name="invalid-tool-provider",
                capabilities=["chat", "tool_call"],
                routing_policy={"fallback": False},
            )
            tool = ModelTool(
                name="knowledge_search",
                description="Search authorized knowledge",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
            with pytest.raises(ModelUnavailableError):
                await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                    session,
                    organization_id=ORGANIZATION_ID,
                    run_id=RUN_ID,
                    step_id=None,
                    profile_id=profile.id,
                    messages=[{"role": "user", "content": "search"}],
                    classification=Classification.INTERNAL,
                    tools=(tool,),
                    tool_choice="required",
                )
            call = await session.scalar(select(ModelCall))
            assert call is not None
            assert call.outcome == "FAILED"
    finally:
        await database.dispose()


def test_agents_and_frontend_do_not_embed_provider_or_model_identifiers() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    governed_roots = [
        repository_root / "services/control-plane/src/obsion/harness",
        repository_root / "services/control-plane/src/obsion/registry",
        repository_root / "apps/web/src",
    ]
    forbidden = (
        "chat/completions",
        "api.openai.com",
        "api.deepseek.com",
        "openai",
        "deepseek",
        "qwen",
        "glm",
        "model_id",
    )
    violations: list[str] = []
    for root in governed_roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"}:
                continue
            source = path.read_text(encoding="utf-8").casefold()
            for marker in forbidden:
                if marker.casefold() in source:
                    violations.append(f"{path.relative_to(repository_root)}: {marker}")
    assert violations == []


def test_model_profile_and_endpoint_admin_contract(client: TestClient) -> None:
    profile = client.post(
        "/api/v1/admin/models/profiles",
        json={
            "name": "phase6-fast",
            "requirements": {
                "capabilities": ["chat", "tool_call", "json_mode"],
                "providers": ["deepseek"],
                "region": "cn-east",
                "min_context_window": 32_000,
                "private": False,
            },
            "routing_policy": {"fallback": True},
            "enabled": True,
        },
    )
    assert profile.status_code == 201, profile.text
    endpoint = client.post(
        "/api/v1/admin/models/endpoints",
        json={
            "name": "phase6-deepseek",
            "provider": "deepseek",
            "base_url": "http://localhost:9999/v1",
            "model_id": "configured-only-in-gateway",
            "region": "cn-east",
            "classifications": ["PUBLIC", "INTERNAL"],
            "capabilities": ["chat", "tool_call", "json_mode"],
            "limits": {
                "context_window": 64_000,
                "max_output_tokens": 8_000,
                "pricing_per_million": {"input": 1, "output": 2},
            },
            "enabled": True,
        },
    )
    assert endpoint.status_code == 201, endpoint.text
    binding = client.post(
        f"/api/v1/admin/models/profiles/{profile.json()['id']}/endpoints",
        json={"endpoint_id": endpoint.json()["id"], "priority": 10},
    )
    assert binding.status_code == 201, binding.text

    profiles = client.get("/api/v1/admin/models/profiles")
    endpoints = client.get("/api/v1/admin/models/endpoints")
    assert profiles.status_code == 200
    assert endpoints.status_code == 200
    created_profile = next(item for item in profiles.json() if item["name"] == "phase6-fast")
    created_endpoint = next(item for item in endpoints.json() if item["name"] == "phase6-deepseek")
    assert created_profile["routing_policy"] == {"fallback": True}
    assert created_profile["requirements"]["providers"] == ["deepseek"]
    assert created_endpoint["model_id"] == "configured-only-in-gateway"
    assert "credential" not in created_endpoint or created_endpoint["credential_ref"] is None
