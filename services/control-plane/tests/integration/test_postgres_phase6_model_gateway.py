import os
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from obsion.common.time import utc_now
from obsion.config import Environment, get_settings
from obsion.db.models import (
    ModelCall,
    ModelEndpoint,
    ModelProfile,
    ModelProfileEndpoint,
    Organization,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.domain.enums import Classification, RunStatus, ThreadStatus, Visibility
from obsion.model_gateway.gateway import ModelGateway


@pytest.mark.asyncio
async def test_postgres_model_fallback_persists_each_attempt_without_prompt() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    settings = get_settings()
    assert settings.environment in {Environment.DEVELOPMENT, Environment.TEST}
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    now = utc_now()
    raw_prompt = f"phase6-secret-free-prompt-{uuid4()}"

    def provider(request: httpx.Request) -> httpx.Response:
        if "/primary/" in request.url.path:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "fallback answer"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    try:
        session.add(
            Organization(
                id=organization_id,
                slug=f"phase6-postgres-{organization_id}",
                name="Phase 6 PostgreSQL",
                active=True,
                settings={},
            )
        )
        await session.flush()
        session.add(
            User(
                id=user_id,
                organization_id=organization_id,
                external_id=f"phase6-{user_id}",
                email=f"{user_id}@example.invalid",
                display_name="Phase 6 user",
                active=True,
                attributes={},
            )
        )
        await session.flush()
        session.add(
            Workspace(
                id=workspace_id,
                organization_id=organization_id,
                name="Phase 6 workspace",
                description="",
                owner_id=user_id,
                classification=Classification.INTERNAL,
                visibility=Visibility.PRIVATE,
            )
        )
        await session.flush()
        session.add(
            Thread(
                id=thread_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                title="Phase 6 thread",
                status=ThreadStatus.ACTIVE,
                created_by=user_id,
            )
        )
        await session.flush()
        session.add(
            Turn(
                id=turn_id,
                organization_id=organization_id,
                thread_id=thread_id,
                ordinal=1,
                created_by=user_id,
                input_text=raw_prompt,
                sanitized_input=raw_prompt,
                context_refs=[],
                attachment_refs=[],
                created_at=now,
            )
        )
        await session.flush()
        profile = ModelProfile(
            organization_id=organization_id,
            name="fast",
            requirements={"capabilities": ["chat"]},
            routing_policy={"fallback": True},
            enabled=True,
        )
        primary = ModelEndpoint(
            organization_id=organization_id,
            name="phase6-primary",
            provider="openai-compatible",
            base_url="http://localhost:9999/primary/v1",
            model_id="primary-model",
            classifications=[Classification.INTERNAL.value],
            capabilities=["chat"],
            limits={
                "context_window": 32_000,
                "max_output_tokens": 2_000,
                "pricing_per_million": {"input": 1, "output": 2},
            },
            enabled=True,
        )
        secondary = ModelEndpoint(
            organization_id=organization_id,
            name="phase6-secondary",
            provider="deepseek",
            base_url="http://localhost:9999/secondary/v1",
            model_id="secondary-model",
            classifications=[Classification.INTERNAL.value],
            capabilities=["chat"],
            limits={
                "context_window": 32_000,
                "max_output_tokens": 2_000,
                "pricing_per_million": {"input": 1, "output": 2},
            },
            enabled=True,
        )
        session.add_all([profile, primary, secondary])
        await session.flush()
        session.add_all(
            [
                ModelProfileEndpoint(
                    profile_id=profile.id,
                    endpoint_id=primary.id,
                    priority=10,
                ),
                ModelProfileEndpoint(
                    profile_id=profile.id,
                    endpoint_id=secondary.id,
                    priority=20,
                ),
            ]
        )
        session.add(
            Run(
                id=run_id,
                organization_id=organization_id,
                turn_id=turn_id,
                status=RunStatus.RUNNING,
                model_profile_id=profile.id,
            )
        )
        await session.flush()

        result = await ModelGateway(
            settings,
            transport=httpx.MockTransport(provider),
        ).complete(
            session,
            organization_id=organization_id,
            run_id=run_id,
            step_id=None,
            profile_id=profile.id,
            messages=[{"role": "user", "content": raw_prompt}],
            classification=Classification.INTERNAL,
        )

        assert result.profile_id == profile.id
        assert result.endpoint_id == secondary.id
        calls = list(
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.organization_id == organization_id)
                .order_by(ModelCall.created_at, ModelCall.id)
            )
        )
        assert [(call.endpoint_id, call.outcome) for call in calls] == [
            (primary.id, "FAILED"),
            (secondary.id, "SUCCESS"),
        ]
        assert calls[1].input_tokens == 11
        assert calls[1].output_tokens == 4
        assert calls[1].cost_amount == Decimal("0.00001900")
        assert all(len(call.request_fingerprint) == 64 for call in calls)
        assert all(raw_prompt not in call.request_fingerprint for call in calls)
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
