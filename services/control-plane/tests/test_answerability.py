"""Synthetic model output through the real Harness and public event projection."""

import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.db.models import Run
from obsion.harness.answerability import GenerationStatus, generation_reply, record_generation
from obsion.harness.runtime import HarnessRuntime
from obsion.model_gateway.gateway import ModelGateway, ModelResult, ModelUnavailableError


@pytest.mark.parametrize(
    "excerpt", ["不在用户问题中的断言", "<script>bad</script>", None, [], "a" * 161]
)
def test_foreign_or_invalid_excerpt_is_never_reflected(excerpt):
    run = Run(plan={"route": "KNOWLEDGE"})
    record_generation(
        run,
        GenerationStatus.INSUFFICIENT_EVIDENCE,
        question="存储容量上限是多少？",
        excerpt=excerpt,
    )
    assert run.plan["answer_generation"] == {"status": "INSUFFICIENT_EVIDENCE"}
    assert "确认这个问题" in generation_reply(run.plan["answer_generation"])


def test_exact_user_excerpt_is_text_and_previous_outcome_is_reset():
    run = Run(plan={"route": "KNOWLEDGE"})
    question = "请确认 [容量](https://example.test) <limit> 是多少？"
    record_generation(
        run,
        GenerationStatus.INSUFFICIENT_EVIDENCE,
        question=question,
        excerpt="[容量](https://example.test) <limit>",
    )
    reply = generation_reply(run.plan["answer_generation"])
    assert "&lt;limit&gt;" in reply and "\\[容量\\]" in reply
    record_generation(run, GenerationStatus.UNASSESSED)
    assert run.plan["answer_generation"] == {"status": "UNASSESSED"}
    assert generation_reply(run.plan["answer_generation"]) is None


def test_revoked_access_takes_precedence_over_an_abstention_topic():
    critic = SimpleNamespace(
        conflicts=[{"reason_codes": ["managed_source_access_changed"]}], missing_evidence=()
    )
    answer = HarnessRuntime._withheld_answer(
        critic,
        generation={"status": "INSUFFICIENT_EVIDENCE", "requested_information": "原来的资料主题"},
    )
    assert "资料权限或版本发生了变化" in answer and "原来的资料主题" not in answer


def test_general_answers_do_not_acquire_enterprise_generation_metadata():
    run = Run(plan={"route": "GENERAL"})
    record_generation(run, GenerationStatus.MODEL_UNAVAILABLE)
    assert run.plan == {"route": "GENERAL"}


@pytest.mark.parametrize(
    "mode",
    ["insufficient", "legacy", "foreign", "contradictory", "malformed", "unavailable", "no_route"],
)
def test_harness_distinguishes_decline_from_invalid_output(client, monkeypatch, mode):
    question = "请根据授权资料回答：单个空间的存储容量上限是多少 GB？"
    malicious = "不能发布的候选：上限为999999 GB。"
    document = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": ("capacity.md", "知识库存储容量：提供充足存储空间。".encode(), "text/markdown")
        },
        data={
            "source": "answerability-test",
            "external_id": "capacity",
            "title": "知识库存储容量",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert document.status_code == 201

    async def complete(self, session, **kwargs):
        if mode in {"unavailable", "no_route"}:
            raise ModelUnavailableError(
                "synthetic model failure", no_model_route=mode == "no_route"
            )
        payload = {
            "answerable": False,
            "answer": malicious,
            "claims": [],
            "missing_information": "单个空间的存储容量上限",
        }
        if mode == "legacy":
            payload.pop("answerable")
        if mode == "foreign":
            payload["missing_information"] = "泄露其他文档中的信息"
        if mode == "contradictory":
            payload["claims"] = [{"statement": malicious, "evidence_ids": ["foreign-id"]}]
        if mode == "malformed":
            payload["answerable"] = "false"
        return ModelResult(
            content=json.dumps(payload),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            cost_amount=Decimal("0"),
            finish_reason="stop",
        )

    monkeypatch.setattr(ModelGateway, "complete", complete)
    thread = _create_thread(client)
    created = client.post("/api/v1/threads/" + thread["id"] + "/turns", json={"input": question})
    assert created.status_code == 202
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED"
    artifacts = client.get("/api/v1/runs/" + run["id"] + "/artifacts").json()
    answer = next(a["inline_content"] for a in artifacts if a["title"] == "Obsion answer")
    assert not answer["verification"]["verified"] and answer["citations"] == []
    assert malicious not in json.dumps(artifacts, ensure_ascii=False)
    assert "999999" not in answer["markdown"] and "泄露其他" not in answer["markdown"]
    expected = (
        "INSUFFICIENT_EVIDENCE"
        if mode in {"insufficient", "legacy", "foreign"}
        else "MODEL_UNAVAILABLE"
        if mode in {"unavailable", "no_route"}
        else "INVALID_OUTPUT"
    )
    assert run["plan"]["answer_generation"]["status"] == expected
    if mode in {"insufficient", "legacy"}:
        assert "确认「单个空间的存储容量上限」" in answer["markdown"]
        assert "本次回答未通过证据核验" not in answer["markdown"]
    events = client.get("/api/v1/runs/" + run["id"] + "/events").json()
    assert [e["payload"]["delta"] for e in events if e["name"] == "answer.delta"] == [
        answer["markdown"]
    ]


@pytest.mark.parametrize("route", ["KNOWLEDGE", "SUPPORT"])
async def test_no_model_profile_cannot_turn_retrieved_text_into_a_verified_answer(route):
    from unittest.mock import AsyncMock

    runtime = HarnessRuntime.__new__(HarnessRuntime)
    runtime.models = AsyncMock()
    run = Run(plan={"route": route}, model_profile_id=None)
    answer, claims = await runtime._synthesize(
        None,
        run,
        SimpleNamespace(sanitized_input="请根据授权资料回答"),
        None,
        None,
        [SimpleNamespace(content={"hits": [{"content": "仅为检索片段"}]})],
        [],
        [],
    )
    assert claims == [] and "仅为检索片段" not in answer
    assert run.plan["answer_generation"] == {"status": "MODEL_UNAVAILABLE"}
    runtime.models.complete.assert_not_called()
