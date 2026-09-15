"""Real publication ordering for independent scores; model/data are synthetic."""

import asyncio
import json
import os
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from test_independent_answer_scoring import judgment
from test_independent_answer_scoring import published as published_fixture
from test_phase6_model_gateway import _completion

from obsion.api.evaluations import get_answer_scoring_service
from obsion.application.answer_scoring import AnswerScoringService
from obsion.config import Environment, Settings
from obsion.db.models import AuditRecord, Document, ModelCall, Policy, Run, User
from obsion.domain.enums import Classification, DecisionEffect
from obsion.knowledge.service import KnowledgeService
from obsion.main import create_app
from obsion.model_gateway.gateway import ModelGateway

published = published_fixture


@pytest.fixture
def client():
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Independent score ordering requires disposable PostgreSQL")
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        enterprise_knowledge_mode="indexed-development",
        database_url=os.environ["OBSION_DATABASE_URL"],
        dev_organization_id=uuid4(),
        dev_user_id=uuid4(),
        dev_bearer_token="synthetic-scoring-postgres",
        allowed_origins=["http://testserver"],
    )
    with TestClient(
        create_app(settings), headers={"Authorization": "Bearer synthetic-scoring-postgres"}
    ) as connected:
        yield connected


async def revoke(session, request, settings, target):
    if target == "document":
        await session.execute(
            update(Document)
            .where(Document.id == request.case.source_document_id)
            .values(acl={"deny_users": [str(settings.dev_user_id)]})
        )
    elif target == "classification":
        await session.execute(
            update(Document)
            .where(Document.id == request.case.source_document_id)
            .values(classification=Classification.RESTRICTED)
        )
    elif target == "user":
        await session.execute(
            update(User).where(User.id == settings.dev_user_id).values(active=False)
        )
    else:
        session.add(
            Policy(
                organization_id=settings.dev_organization_id,
                name="concurrent-deny-independent-score",
                version=1,
                priority=1000,
                effect=DecisionEffect.DENY,
                conditions={"actions": ["evaluations.write"]},
                obligations=[],
                reason="synthetic concurrent revocation",
                created_by=settings.dev_user_id,
                enabled=True,
            )
        )
    await session.flush()


@pytest.mark.parametrize("target", ["document", "classification", "user", "policy"])
def test_committed_revocation_during_model_blocks_score(client, published, target):
    request, answer, original = published
    settings = client.app.state.settings
    sessions = client.app.state.database.sessions
    commits = []

    async def provider(http_request):
        # This is a separate real transaction while the score's model request
        # is in flight. Publication must not hold a fence across model work.
        async with asyncio.timeout(5), sessions() as writer, writer.begin():
            await revoke(writer, request, settings, target)
        commits.append(True)
        return httpx.Response(200, json=_completion(json.dumps(judgment(answer))))

    service = AnswerScoringService(
        KnowledgeService(settings, client.app.state.object_store),
        ModelGateway(settings, transport=httpx.MockTransport(provider)),
    )
    client.app.dependency_overrides[get_answer_scoring_service] = lambda: service
    response = client.post(
        "/api/v1/admin/evaluations/answer-score", json=request.model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    score = response.json()
    assert commits == [True]
    assert score["status"] == "BLOCKED", score
    assert len(score["evidence"]) == 1  # No stale semantic/quote evidence survives.

    async def verify():
        async with sessions() as session:
            audit = await session.get(AuditRecord, UUID(score["evidence"][0]["audit_id"]))
            call = await session.scalar(
                select(ModelCall).where(ModelCall.profile_id == request.model_profile_id)
            )
            run = await session.get(Run, request.run_id)
            assert audit.outcome == "BLOCKED" and audit.policy_decision_id
            assert call.run_id is None and call.outcome == "SUCCESS"
            assert audit.redacted_metadata["model_call_ids"] == [str(call.id)]
            assert run.status.value == original["status"]
            assert run.input_tokens == original["input_tokens"]
            assert run.output_tokens == original["output_tokens"]

    client.portal.call(verify)


def test_score_commit_is_ordered_before_later_revocation(client, published):
    request, answer, _ = published
    settings = client.app.state.settings
    sessions = client.app.state.database.sessions
    writers = []

    async def provider(http_request):
        return httpx.Response(200, json=_completion(json.dumps(judgment(answer))))

    class ObservedService(AnswerScoringService):
        reads = 0

        async def _inputs(self, session, principal, req):
            result = await super()._inputs(session, principal, req)
            self.reads += 1
            if self.reads != 2:
                return result
            publisher_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            writer = sessions()
            writers.append(writer)
            writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))

            async def write_and_commit():
                await revoke(writer, req, settings, "document")
                await writer.commit()

            self.write_task = asyncio.create_task(write_and_commit())
            async with sessions() as observer, asyncio.timeout(5):
                while True:
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": writer_pid}
                    )
                    if publisher_pid in blockers:
                        break
                    if self.write_task.done():
                        await self.write_task
                        self.revocation_overtook_publication = True
                        raise AssertionError("Revocation overtook score publication")
                    await asyncio.sleep(0.01)
            return result

    service = ObservedService(
        KnowledgeService(settings, client.app.state.object_store),
        ModelGateway(settings, transport=httpx.MockTransport(provider)),
    )
    client.app.dependency_overrides[get_answer_scoring_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/admin/evaluations/answer-score", json=request.model_dump(mode="json")
        )
        assert not getattr(service, "revocation_overtook_publication", False), (
            "A separate transaction revoked source access "
            "after final checks and before score commit"
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "PASS"

        async def complete_writer():
            await asyncio.wait_for(service.write_task, 5)

        client.portal.call(complete_writer)
        repeated = client.post(
            "/api/v1/admin/evaluations/answer-score", json=request.model_dump(mode="json")
        )
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "BLOCKED"
    finally:

        async def cleanup():
            task = getattr(service, "write_task", None)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for writer in writers:
                await writer.close()

        client.portal.call(cleanup)
