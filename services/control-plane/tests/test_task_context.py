"""Task scope regressions using local documents and an explicit synthetic author."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.api.schemas import CreateTurnRequest
from obsion.db.models import Document, Run
from obsion.domain.task_context import task_prompt
from obsion.harness.grounding import GroundingAssessment
from obsion.harness.runtime import HarnessRuntime


@pytest.mark.parametrize(
    "reference",
    [
        {"type": "task_context", "document_ids": ["invalid"]},
        {"type": "task_context", "document_ids": [str(uuid4())] * 2},
        {"type": "task_context", "output_format": "EXECUTE"},
        {"type": "task_context", "constraints": [" "]},
        {"type": "task_context", "credentials": "not-allowed"},
    ],
)
def test_task_context_rejects_invalid_input(reference):
    with pytest.raises(ValidationError):
        CreateTurnRequest(input="解释资料", context_refs=[reference])


def test_task_context_distinguishes_omitted_and_explicit_clear():
    omitted = CreateTurnRequest(input="继续", context_refs=[{"type": "task_context"}])
    cleared = CreateTurnRequest(
        input="继续", context_refs=[{"type": "task_context", "document_ids": []}]
    )
    assert omitted.context_refs == [{"type": "task_context"}]
    assert cleared.context_refs == [{"type": "task_context", "document_ids": []}]
    with pytest.raises(ValidationError):
        CreateTurnRequest(input="继续", context_refs=[{"type": "task_context"}] * 2)


@pytest.fixture
def scoped_documents(client, monkeypatch):
    fact = "青禾采购制度要求财务复核。"
    identifiers = []
    for index, content in enumerate([fact, "其他资料的采购要求是行政复核。"]):
        response = client.post(
            "/api/v1/knowledge/documents",
            files={"file": (f"scope-{index}.md", content.encode(), "text/markdown")},
            data={
                "source": "native-p1",
                "external_id": f"scope-{index}",
                "title": f"采购制度-{index}",
                "classification": "INTERNAL",
                "acl": '{"organization":true}',
            },
        )
        assert response.status_code == 201, response.text
        identifiers.append(response.json()["document"]["id"])

    async def synthesize(self, *args):
        evidence = args[5]
        return fact, [{"statement": fact, "evidence_ids": [str(evidence[0].id)], "confidence": 0.9}]

    async def review(models, session, **kwargs):
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

    monkeypatch.setattr(HarnessRuntime, "_synthesize", synthesize)
    monkeypatch.setattr("obsion.harness.runtime.review_knowledge_answer", review)
    return identifiers


def test_normal_turns_inherit_scope_format_constraints_and_can_clear(client, scoped_documents):
    thread = _create_thread(client)

    async def intent_for(identifier):
        async with client.app.state.database.sessions() as session:
            return (await session.scalar(select(Run).where(Run.id == UUID(identifier)))).intent

    for index, question in enumerate(
        ["所选采购制度要求什么？", "改成表格", "请再详细解释一下", "不要用表格", "继续"]
    ):
        payload = {"input": question}
        if index == 0:
            payload["context_refs"] = [
                {
                    "type": "task_context",
                    "document_ids": scoped_documents[:1],
                    "output_format": "REPORT",
                    "constraints": ["只描述复核要求"],
                }
            ]
        if index == 4:
            payload["context_refs"] = [
                {
                    "type": "task_context",
                    "document_ids": [],
                    "output_format": "AUTO",
                    "constraints": [],
                }
            ]
        created = client.post(f"/api/v1/threads/{thread['id']}/turns", json=payload)
        assert created.status_code == 202, created.text
        run = _wait_terminal(client, created.json()["run"]["id"])
        assert run["status"] == "COMPLETED", run
        intent = client.portal.call(intent_for, run["id"])
        slots = {slot["slot"]: slot for slot in intent["resolved_slots"]}
        if index < 4:
            assert slots["selected_documents"]["value"] == [
                {"document_id": scoped_documents[0], "version": 1}
            ]
            expected_format = ["REPORT", "TABLE", "TABLE", "AUTO"][index]
            assert slots["output_format"]["value"] == expected_format
            assert run["intent"]["task_context"]["output_format"] == expected_format
            assert slots["task_constraints"]["value"] == ["只描述复核要求"]
            assert all(
                step["capability"] == "document.read"
                and step["payload"]["document_id"] == scoped_documents[0]
                for step in run["plan"]["steps"]
            )
            assert run["plan"]["steps"]
            assert "只描述复核要求" in task_prompt(intent, question)
        else:
            assert slots["selected_documents"]["value"] == []
            assert slots["task_constraints"]["value"] == []
            assert slots["output_format"]["value"] == "AUTO"
            assert run["plan"]["steps"][0]["capability"] == "knowledge.search"


def test_inaccessible_selected_source_is_never_planned(client, scoped_documents):
    async def revoke():
        async with client.app.state.database.sessions() as session, session.begin():
            await session.execute(
                update(Document)
                .where(Document.id == UUID(scoped_documents[0]))
                .values(acl={"deny_roles": ["admin"]})
            )

    client.portal.call(revoke)
    thread = _create_thread(client)
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={
            "input": "解释所选资料",
            "context_refs": [{"type": "task_context", "document_ids": scoped_documents[:1]}],
        },
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "FAILED", run
    assert not run["plan"].get("steps")


@pytest.mark.parametrize("stage", ["before_snapshot", "after_snapshot"])
def test_revoked_latest_turn_cannot_fall_back_to_an_older_task(
    client, scoped_documents, monkeypatch, stage
):
    thread = _create_thread(client)
    for payload in [
        {"input": "你好"},
        {
            "input": "所选采购制度要求什么？",
            "context_refs": [{"type": "task_context", "document_ids": scoped_documents[:1]}],
        },
    ]:
        created = client.post(f"/api/v1/threads/{thread['id']}/turns", json=payload)
        assert created.status_code == 202, created.text
        _wait_terminal(client, created.json()["run"]["id"])

    async def revoke():
        async with client.app.state.database.sessions() as session, session.begin():
            await session.execute(
                update(Document)
                .where(Document.id == UUID(scoped_documents[0]))
                .values(acl={"deny_roles": ["admin"]})
            )

    if stage == "before_snapshot":
        client.portal.call(revoke)
    else:
        from obsion.knowledge.publication import KnowledgePublicationGuard

        original = KnowledgePublicationGuard.check

        async def check(self, *args, **kwargs):
            if kwargs["stage"] == "intent_context":
                return False
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(KnowledgePublicationGuard, "check", check)
    created = client.post(f"/api/v1/threads/{thread['id']}/turns", json={"input": "继续"})
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "FAILED", run
    assert not run["plan"].get("steps")


@pytest.mark.asyncio
async def test_investigator_cannot_search_or_read_outside_selected_scope():
    import json
    from decimal import Decimal
    from types import SimpleNamespace

    from obsion.domain.enums import Classification
    from obsion.harness.investigation import investigation_catalog, propose_investigation
    from obsion.model_gateway.gateway import ModelResult

    selected_id, other_id = str(uuid4()), str(uuid4())
    run = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        intent={
            "resolved_slots": [
                {
                    "slot": "selected_documents",
                    "value": [{"document_id": selected_id, "version": 1}],
                }
            ]
        },
        plan={"available_capabilities": ["knowledge.search", "document.read"]},
        max_input_tokens=10000,
        input_tokens=0,
        max_output_tokens=5000,
        output_tokens=0,
        max_cost_amount=10,
        cost_amount=0,
        model_profile_id=uuid4(),
    )
    step = SimpleNamespace(
        id=uuid4(),
        ordinal=1,
        input_payload={"capability": "knowledge.search", "payload": {"query": "采购", "limit": 8}},
    )
    evidence = SimpleNamespace(
        organization_id=run.organization_id,
        run_id=run.id,
        step_id=step.id,
        evidence_type="DOCUMENT",
        content={
            "hits": [
                {"document_id": selected_id, "version": 1, "content": "所选资料正文"},
                {"document_id": other_id, "version": 1, "content": "其他资料不应进入调查"},
            ]
        },
    )
    targets, bodies = investigation_catalog(run, [evidence], [step])
    assert len(targets) == 1 and next(iter(targets.values()))["document_id"] == selected_id
    assert [body["text"] for body in bodies] == ["所选资料正文"]

    class Models:
        async def complete(self, session, **kwargs):
            context = json.loads(kwargs["messages"][1]["content"])
            assert context["allowed_actions"] == ["READ", "STOP"]
            # Deliberately violate the model instructions; server must reject it.
            return ModelResult(
                content='{"action":"SEARCH","query":"其他资料"}',
                profile_id=run.model_profile_id,
                endpoint_id=uuid4(),
                input_tokens=10,
                output_tokens=10,
                cost_amount=Decimal(".01"),
                latency_ms=1,
                finish_reason="stop",
            )

    contract, diagnostic = await propose_investigation(
        Models(),
        None,
        run=run,
        step_id=None,
        question="解释所选资料",
        evidence=[evidence],
        steps=[step],
        reason="incomplete",
        classification=Classification.INTERNAL,
    )
    assert contract is None and diagnostic["status"] == "invalid"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("不要用表格", "AUTO"),
        ("请不要再用表格", "AUTO"),
        ("不用列表", "AUTO"),
        ("改成表格", "TABLE"),
        ("请用列表形式解释", "BULLETS"),
        ("用报告总结", "REPORT"),
        ("以段落呈现", "AUTO"),
        ("文档说“用表格回答”是什么意思？", None),
        ("为什么代码中使用表格？", None),
        ("用表格说明采购制度", None),
    ],
)
def test_format_preference_requires_a_complete_user_selection(question, expected):
    from obsion.domain.task_context import requested_format
    from obsion.harness.understanding import is_contextual_followup

    assert requested_format(question) == expected
    if expected is not None:
        assert is_contextual_followup(question)
