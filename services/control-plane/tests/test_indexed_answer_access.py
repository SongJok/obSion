"""Native indexed source revocation; synthetic model, real Harness and HTTP reads."""

from uuid import UUID

import pytest
from sqlalchemy import select, update
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.common.time import utc_now
from obsion.db.models import Artifact, AuditRecord, Document
from obsion.harness.grounding import GroundingAssessment
from obsion.harness.runtime import HarnessRuntime
from obsion.knowledge.publication import KnowledgePublicationGuard


@pytest.mark.parametrize(
    "change", ["none", "before_author", "author", "review", "after_publish", "deleted", "version"]
)
def test_indexed_document_is_rechecked_before_publication_and_historical_reads(
    client, monkeypatch, change
):
    fact = "青禾采购制度要求财务复核。"
    response = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("purchasing.md", fact.encode(), "text/markdown")},
        data={
            "source": "native-p1",
            "external_id": "purchasing",
            "title": "青禾采购制度",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert response.status_code == 201, response.text
    document_id = UUID(response.json()["document"]["id"])
    authors, reviews = [], []
    original_check = KnowledgePublicationGuard.check

    async def revoke(session):
        values = {"acl": {"deny_roles": ["admin"]}}
        if change == "deleted":
            values = {"deleted_at": utc_now()}
        elif change == "version":
            # The source now points at a different version; old Evidence stays frozen.
            values = {"current_version": Document.current_version + 1}
        await session.execute(update(Document).where(Document.id == document_id).values(**values))
        await session.flush()

    async def check(self, session, principal, evidence, *, run_id, stage, **kwargs):
        if stage == "before_author" and change == "before_author":
            await revoke(session)
        return await original_check(
            self, session, principal, evidence, run_id=run_id, stage=stage, **kwargs
        )

    async def synthesize(self, *args):
        session, evidence = args[0], args[5]
        authors.append(True)
        if change == "author":
            await revoke(session)
        return fact, [{"statement": fact, "evidence_ids": [str(evidence[0].id)], "confidence": 0.9}]

    async def review(models, session, **kwargs):
        reviews.append(True)
        if change == "review":
            await revoke(session)
        return GroundingAssessment(
            accepted=True,
            reason_code="supported",
            candidate_fingerprint="a" * 64,
            input_fingerprint="b" * 64,
            claims=(
                {
                    "claim_index": 1,
                    "verdict": "SUPPORTED",
                    "quotes": [
                        {
                            "evidence_id": str(kwargs["evidence"][0].id),
                            "body_index": 0,
                            "quote": fact,
                        }
                    ],
                },
            ),
        )

    monkeypatch.setattr(KnowledgePublicationGuard, "check", check)
    monkeypatch.setattr(HarnessRuntime, "_synthesize", synthesize)
    monkeypatch.setattr("obsion.harness.runtime.review_knowledge_answer", review)
    thread = _create_thread(client)
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "青禾采购制度要求什么？"}
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run

    async def persisted_answer():
        async with client.app.state.database.sessions() as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == UUID(run["id"]),
                    Artifact.title == "Obsion answer",
                )
            )
            assert artifact is not None
            return artifact.inline_content

    answer = client.portal.call(persisted_answer)
    if change in {"before_author", "author", "review"}:
        assert fact not in answer["markdown"]
        assert answer["verification"]["verified"] is False
        assert len(authors) == (0 if change == "before_author" else 1)
        assert len(reviews) == (1 if change == "review" else 0)
    else:
        assert fact in answer["markdown"] and answer["verification"]["verified"]
    if change in {"after_publish", "deleted", "version"}:

        async def revoke_after_publish():
            async with client.app.state.database.sessions() as session, session.begin():
                await revoke(session)

        client.portal.call(revoke_after_publish)
    if change != "none":
        for suffix in ("artifacts", "evidence", "events", "claims", "conversation"):
            assert client.get(f"/api/v1/runs/{run['id']}/{suffix}").status_code == 404
        metadata = client.get(f"/api/v1/runs/{run['id']}").json()
        assert metadata["source_content_available"] is False
        assert metadata["intent"] == {} and metadata["plan"] == {}
        assert client.post(f"/api/v1/runs/{run['id']}/replay").status_code == 404

        async def denial_audit():
            async with client.app.state.database.sessions() as session:
                record = await session.scalar(
                    select(AuditRecord).where(
                        AuditRecord.resource_id == str(document_id),
                        AuditRecord.outcome == "DENIED",
                    )
                )
                assert record is not None and record.policy_decision_id is not None

        client.portal.call(denial_audit)
    else:
        assert client.get(f"/api/v1/runs/{run['id']}/artifacts").status_code == 200
