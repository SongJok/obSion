from pathlib import Path

import httpx
import pytest
from test_phase6_model_gateway import ORGANIZATION_ID, RUN_ID, _completion, _database, _seed_profile

from obsion.db.models import ModelEndpoint, ModelProfileEndpoint, Organization
from obsion.domain.enums import Classification
from obsion.model_gateway.gateway import ModelGateway, ModelUnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["chat", "embeddings"])
@pytest.mark.parametrize("allowed", [[], ["kimi-k3-kimi"], ["OLD-MODEL"]])
async def test_disallowed_model_never_reaches_transport(tmp_path: Path, operation, allowed):
    settings, database = await _database(tmp_path, "denied.db")
    settings.model_allowed_ids = allowed
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(500)

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID, slug="allowlist", name="Allowlist", active=True, settings={}
                )
            )
            profile, endpoint = await _seed_profile(
                session, name="selected", endpoint_name="old", capabilities=["chat", "embeddings"]
            )
            endpoint.model_id = "old-model"
            await session.flush()
            gateway = ModelGateway(settings, transport=httpx.MockTransport(provider))
            with pytest.raises(ModelUnavailableError) as error:
                if operation == "chat":
                    await gateway.complete(
                        session,
                        organization_id=ORGANIZATION_ID,
                        run_id=RUN_ID,
                        step_id=None,
                        profile_id=profile.id,
                        messages=[{"role": "user", "content": "test"}],
                        classification=Classification.INTERNAL,
                    )
                else:
                    await gateway.embed(
                        session,
                        organization_id=ORGANIZATION_ID,
                        profile_name=profile.name,
                        texts=["test"],
                        classification=Classification.INTERNAL,
                    )
            if operation == "chat":
                assert error.value.no_model_route is True
            assert calls == []
    finally:
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_primary", [False, True])
async def test_allowlist_filters_priority_and_fallback(tmp_path: Path, fail_primary: bool):
    settings, database = await _database(tmp_path, "fallback.db")
    settings.model_allowed_ids = ["kimi-k3-kimi", "kimi-k2.7-kimi"]
    requested = []

    def provider(request):
        import json

        model = json.loads(request.content)["model"]
        requested.append(model)
        if fail_primary and model == "kimi-k3-kimi":
            return httpx.Response(503)
        return httpx.Response(200, json=_completion("ok"))

    try:
        async with database.sessions() as session, session.begin():
            session.add(
                Organization(
                    id=ORGANIZATION_ID, slug="allowlist", name="Allowlist", active=True, settings={}
                )
            )
            profile, old = await _seed_profile(
                session, name="selected", endpoint_name="old", priority=1
            )
            for name, priority in [("kimi-k3-kimi", 10), ("kimi-k2.7-kimi", 20)]:
                ep = ModelEndpoint(
                    organization_id=ORGANIZATION_ID,
                    name=name,
                    provider=old.provider,
                    base_url=old.base_url,
                    model_id=name,
                    classifications=old.classifications,
                    capabilities=old.capabilities,
                    limits=old.limits,
                    enabled=True,
                )
                session.add(ep)
                await session.flush()
                session.add(
                    ModelProfileEndpoint(
                        profile_id=profile.id, endpoint_id=ep.id, priority=priority
                    )
                )
            await session.flush()
            result = await ModelGateway(settings, transport=httpx.MockTransport(provider)).complete(
                session,
                organization_id=ORGANIZATION_ID,
                run_id=RUN_ID,
                step_id=None,
                profile_id=profile.id,
                messages=[{"role": "user", "content": "test"}],
                classification=Classification.INTERNAL,
                json_mode=False,
            )
            assert result.content == "ok"
            assert requested == (
                ["kimi-k3-kimi", "kimi-k2.7-kimi"] if fail_primary else ["kimi-k3-kimi"]
            )
    finally:
        await database.dispose()
