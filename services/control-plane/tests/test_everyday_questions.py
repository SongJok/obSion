"""Daily text answers must not manufacture enterprise evidence or tool success."""

import json
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.application.im_identity import _classify_intent
from obsion.db.models import AgentDefinition, AgentVersion, Run, Turn
from obsion.domain.enums import Classification
from obsion.harness.general import everyday_request, general_unavailable_answer
from obsion.harness.runtime import HarnessRuntime
from obsion.model_gateway.gateway import ModelGateway, ModelResult, ModelUnavailableError


@pytest.mark.parametrize(
    "question",
    [
        "你是谁？能怎样帮助我？",
        "请用通俗语言解释什么是机器学习，并给一个日常生活中的例子。",
        "把这句话翻译成英文：明天见。",
        "写一份请假邮件草稿",
        "请写一个 Python 排序函数示例",
        "为什么天空是蓝色？",
        "What is machine learning?",
        "Draft a polite thank-you note.",
        "Explain how git works.",
        "今天上海天气怎么样？",
        "谢谢你！",
        "我有点累",
        "帮我算 12*7",
        "1 + 1 等于多少？",
    ],
)
def test_clear_general_requests(question: str) -> None:
    assert everyday_request(question, context_refs=[])
    assert _classify_intent(question)[0].value == "QUERY"


@pytest.mark.parametrize(
    "question",
    [
        "日常问答：请根据已授权 README 解释 Obsion",
        "请解释我们公司的转化率口径",
        "解释生产库的数据",
        "请修改本仓库代码",
        "起草并发送邮件给主管",
        "What is our revenue?",
        "What does the release policy require?",
        "What is the unrecorded retention exception?",
        "Explain the p99 incident using logs",
        "Obsion 是什么？",
        "帮我创建一个待办任务",
        "请翻译附件",
        "解释一下 https://internal.example/report",
        "根据已授权报表计算 12*7",
        "计算我们公司的收入",
    ],
)
def test_enterprise_sources_and_actions_take_priority(question: str) -> None:
    assert not everyday_request(question, context_refs=[])


def test_refs_and_followups_cannot_downgrade_enterprise_context() -> None:
    assert not everyday_request(
        "解释什么是机器学习", context_refs=[{"type": "artifact", "id": "x"}]
    )
    assert not everyday_request("再短一点", context_refs=[], previous_route="KNOWLEDGE")
    assert everyday_request("再短一点", context_refs=[], previous_route="GENERAL")


def model_result(payload: Any, profile_id: Any) -> ModelResult:
    return ModelResult(
        content=json.dumps(payload, ensure_ascii=False),
        profile_id=profile_id,
        endpoint_id=uuid4(),
        input_tokens=30,
        output_tokens=20,
        latency_ms=1,
        cost_amount=Decimal("0.01"),
        finish_reason="stop",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"response_kind": "GENERAL", "answer": "机器学习从例子中学习规律。", "claims": []},
        {"response_kind": "GENERAL", "answer": "不应发布", "claims": [{"statement": "伪造"}]},
        {"answer": "不应发布", "claims": []},
        {"response_kind": "GENERAL", "answer": " ", "claims": []},
        ModelUnavailableError("offline"),
    ],
)
async def test_daily_output_scope_egress_and_budget(payload: Any) -> None:
    runtime = HarnessRuntime.__new__(HarnessRuntime)
    runtime.models = cast(Any, AsyncMock())
    run = Run(
        id=uuid4(),
        organization_id=uuid4(),
        model_profile_id=uuid4(),
        plan={"route": "GENERAL", "steps": [], "required_evidence": []},
        intent={},
        workspace_context={
            "classification": "RESTRICTED",
            "description": "PRIVATE_WORKSPACE_DESCRIPTION",
        },
        max_input_tokens=10000,
        max_output_tokens=1000,
        max_cost_amount=Decimal("1"),
        input_tokens=0,
        output_tokens=0,
        cost_amount=Decimal("0"),
    )
    if isinstance(payload, Exception):
        runtime.models.complete.side_effect = payload
    else:
        runtime.models.complete.return_value = model_result(payload, run.model_profile_id)
    answer, claims = await runtime._synthesize(
        cast(AsyncSession, None),
        run,
        Turn(sanitized_input="解释什么是机器学习", context_refs=[]),
        AgentVersion(spec={"credentials": "NEVER_A_MODEL_CAPABILITY"}),
        AgentDefinition(name="general-agent"),
        [],
        [],
        [],
    )
    good = isinstance(payload, dict) and payload.get("answer", "").startswith("机器学习")
    assert answer == (payload["answer"] if good else general_unavailable_answer())
    assert claims == []
    call = runtime.models.complete.call_args.kwargs
    assert call["classification"] == Classification.RESTRICTED
    assert "PRIVATE_WORKSPACE_DESCRIPTION" not in str(call["messages"])
    assert "NEVER_A_MODEL_CAPABILITY" not in str(call["messages"])
    assert run.cost_amount == Decimal("0" if isinstance(payload, Exception) else "0.01")


def test_daily_answer_is_auditable_and_followup_keeps_only_daily_context(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def complete(self: ModelGateway, session: AsyncSession, **kwargs: Any) -> ModelResult:
        calls.append(kwargs)
        return model_result(
            {
                "response_kind": "GENERAL",
                "answer": "机器学习从例子中学习规律，例如识别垃圾邮件。",
                "claims": [],
            },
            kwargs["profile_id"],
        )

    monkeypatch.setattr(ModelGateway, "complete", complete)
    thread = _create_thread(client)
    for question in ("解释什么是机器学习", "再短一点"):
        created = client.post(f"/api/v1/threads/{thread['id']}/turns", json={"input": question})
        assert created.status_code == 202, created.text
        run = _wait_terminal(client, created.json()["run"]["id"])
        assert run["status"] == "COMPLETED", run
        assert run["plan"]["route"] == "GENERAL"
        assert run["plan"]["steps"] == []
        assert client.get(f"/api/v1/runs/{run['id']}/evidence").json() == []
        assert client.get(f"/api/v1/runs/{run['id']}/claims").json() == []
        artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
        assert len(artifacts) == 1
        content = artifacts[0]["inline_content"]
        assert content["response_kind"] == "GENERAL"
        assert content["verification_scope"] == "not_applicable"
        assert content["markdown"].startswith("机器学习")
        assert content["verification"]["verified"] is False
        assert content["verification"]["confidence"] == 0
        assert "verification_assessment_id" not in content
        assert content["citations"] == []
        steps = client.get(f"/api/v1/runs/{run['id']}/steps").json()
        assert next(s for s in steps if s["kind"] == "REFLECT")["output_ref"] == "reflect.respond"
        events = client.get(f"/api/v1/runs/{run['id']}/events").json()
        assert "tool.started" not in [e["name"] for e in events]
        assert [e["payload"]["delta"] for e in events if e["name"] == "answer.delta"] == [
            content["markdown"]
        ]
    assert len(calls) == 2
    assert "垃圾邮件" in str(calls[1]["messages"])
    replay = client.post(f"/api/v1/runs/{run['id']}/replay")
    assert replay.status_code == 202, replay.text
    replayed = _wait_terminal(client, replay.json()["id"])
    assert replayed["status"] == "COMPLETED", replayed
    copy = client.get(f"/api/v1/runs/{replayed['id']}/artifacts").json()[0]["inline_content"]
    assert copy["response_kind"] == "GENERAL"
    assert not copy["verification"]["verified"]
    assert copy["markdown"] == content["markdown"]
    assert len(calls) == 2  # A replay must not make a fresh model call.
    enterprise = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "README ONLY_ENTERPRISE_CONTEXT"}
    )
    enterprise_run = _wait_terminal(client, enterprise.json()["run"]["id"])
    assert enterprise_run["plan"]["route"] == "KNOWLEDGE"
    general = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "解释什么是机器学习"}
    )
    general_run = _wait_terminal(client, general.json()["run"]["id"])
    assert general_run["status"] == "COMPLETED"
    assert general_run["plan"]["route"] == "GENERAL"
    assert "ONLY_ENTERPRISE_CONTEXT" not in str(calls[-1]["messages"])


def test_daily_no_model_and_live_information_are_honest(client: TestClient) -> None:
    thread = _create_thread(client)
    for question, expected in (
        ("你是谁", "未能生成日常回答"),
        ("今天上海天气怎么样", "没有查询实时信息的来源"),
    ):
        created = client.post(f"/api/v1/threads/{thread['id']}/turns", json={"input": question})
        run = _wait_terminal(client, created.json()["run"]["id"])
        assert run["status"] == "COMPLETED", run
        content = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()[0]["inline_content"]
        assert expected in content["markdown"]
        assert not content["verification"]["verified"]


def test_real_inbox_transport_metadata_preserves_general_route(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_im_inbox import MESSAGE, provision

    async def complete(self: ModelGateway, session: AsyncSession, **kwargs: Any) -> ModelResult:
        return model_result(
            {"response_kind": "GENERAL", "answer": "机器学习从例子中学习规律。", "claims": []},
            kwargs["profile_id"],
        )

    monkeypatch.setattr(ModelGateway, "complete", complete)
    installation = provision(client, robot=True)
    response = client.post(
        installation["url"],
        json={
            **MESSAGE,
            "conversation_type": "direct",
            "text": "请用通俗语言解释什么是机器学习，并举例",
        },
    )
    assert response.status_code == 202, response.text
    processed = client.post(f"{installation['url']}/{response.json()['id']}/process")
    assert processed.status_code == 200, processed.text
    run = _wait_terminal(client, processed.json()["run_id"])
    assert run["status"] == "COMPLETED", run
    assert run["plan"]["route"] == "GENERAL"
    content = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()[0]["inline_content"]
    assert content["response_kind"] == "GENERAL"
    assert content["markdown"] == "机器学习从例子中学习规律。"
    assert not content["verification"]["verified"]
