from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from obsion.common.errors import BudgetExceededError
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
from obsion.model_gateway.gateway import ModelGateway


@pytest.mark.asyncio
async def test_embedding_calls_are_routed_validated_and_audited(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'embeddings.db'}",
    )
    database = Database(settings)
    organization_id = UUID("00000000-0000-7000-8000-000000000011")

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.25] * 1536},
                    {"index": 1, "embedding": [0.5] * 1536},
                ],
                "usage": {"prompt_tokens": 12, "total_tokens": 12},
            },
        )

    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            organization = Organization(
                id=organization_id,
                slug="embedding-test",
                name="Embedding Test",
                active=True,
                settings={},
            )
            profile = ModelProfile(
                organization_id=organization_id,
                name="knowledge-embedding",
                requirements={},
                routing_policy={},
                enabled=True,
            )
            endpoint = ModelEndpoint(
                organization_id=organization_id,
                name="local-embedding",
                provider="openai-compatible",
                base_url="http://localhost:9999/v1",
                model_id="embedding-model",
                classifications=["INTERNAL"],
                capabilities=["embeddings"],
                limits={"pricing_per_million": {"embedding": 0.02}},
                enabled=True,
            )
            session.add_all([organization, profile, endpoint])
            await session.flush()
            session.add(
                ModelProfileEndpoint(
                    profile_id=profile.id,
                    endpoint_id=endpoint.id,
                    priority=10,
                )
            )
            await session.flush()
            result = await ModelGateway(settings, transport=httpx.MockTransport(provider)).embed(
                session,
                organization_id=organization_id,
                profile_name=profile.name,
                texts=["first", "second"],
                classification=Classification.INTERNAL,
            )

            assert len(result.embeddings) == 2
            assert len(result.embeddings[0]) == 1536
            call = await session.scalar(select(ModelCall))
            assert call is not None
            assert call.operation == "EMBEDDING"
            assert call.run_id is None
            assert call.input_tokens == 12
            assert call.outcome == "SUCCESS"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_chat_request_is_rejected_before_provider_when_budget_is_exhausted(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'chat-budget.db'}",
    )
    database = Database(settings)
    organization_id = UUID("00000000-0000-7000-8000-000000000021")
    called = False

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            organization = Organization(
                id=organization_id,
                slug="chat-budget-test",
                name="Chat Budget Test",
                active=True,
                settings={},
            )
            profile = ModelProfile(
                organization_id=organization_id,
                name="reasoning-high",
                requirements={"capabilities": ["chat"], "min_context_window": 16_000},
                routing_policy={},
                enabled=True,
            )
            endpoint = ModelEndpoint(
                organization_id=organization_id,
                name="local-chat",
                provider="openai-compatible",
                base_url="http://localhost:9999/v1",
                model_id="chat-model",
                classifications=["INTERNAL"],
                capabilities=["chat"],
                limits={"context_window": 32_000, "max_output_tokens": 4_000},
                enabled=True,
            )
            session.add_all([organization, profile, endpoint])
            await session.flush()
            session.add(
                ModelProfileEndpoint(
                    profile_id=profile.id,
                    endpoint_id=endpoint.id,
                    priority=10,
                )
            )
            await session.flush()
            with pytest.raises(BudgetExceededError) as captured:
                await ModelGateway(
                    settings,
                    transport=httpx.MockTransport(provider),
                ).complete(
                    session,
                    organization_id=organization_id,
                    run_id=UUID("00000000-0000-7000-8000-000000000022"),
                    step_id=None,
                    profile_id=profile.id,
                    messages=[{"role": "user", "content": "A request larger than one token"}],
                    classification=Classification.INTERNAL,
                    max_input_tokens=1,
                )
            assert captured.value.details["budget"] == "input_tokens"
            assert not called
            assert await session.scalar(select(ModelCall)) is None
    finally:
        await database.dispose()
