"""P1 development regressions; these are not live/held-out business acceptance."""

import json
import unicodedata
from pathlib import Path

import pytest

from obsion.evaluations.offline import execute_offline_evaluations
from obsion.harness.general import everyday_request
from obsion.harness.understanding import UnderstandingEngine, task_question
from obsion.model_gateway.context import (
    ContextBuilder,
    ContextSegment,
    TrustLevel,
    summarize_segment,
)


@pytest.mark.parametrize(
    ("question", "route"),
    [
        ("代码评审规范", "KNOWLEDGE"),
        ("请解释代码评审规范", "KNOWLEDGE"),
        ("我们公司的编码规范是什么？", "KNOWLEDGE"),
        ("发布流程有哪些要求？", "KNOWLEDGE"),
        ("日志规范", "KNOWLEDGE"),
        ("故障处理指南", "KNOWLEDGE"),
        ("根据代码评审规范分析 src/auth.py 的实现", "ENGINEERING"),
        ("为什么这段代码返回空列表？", "ENGINEERING"),
        ("查找 repository 中的登录代码", "ENGINEERING"),
        ("读取最新 commit 的 diff", "ENGINEERING"),
        ("根据规范定位源码中的调用关系", "ENGINEERING"),
        ("发布之后 p99 异常升高", "INCIDENT"),
        ("日志中出现连接超时", "INCIDENT"),
        ("代码发布异常的根因是什么？", "INCIDENT"),
        ("生产数据库访问要求", "RESOURCE_ACCESS"),
        ("查看 k8s pod 副本", "OPERATION"),
        ("查看用户退款申请工单", "SUPPORT"),
        ("我们公司的差旅报销上限是多少？", "KNOWLEDGE"),
        ("根据接入指南，连接方式是什么？", "KNOWLEDGE"),
        ("你好！", "CONVERSATION"),
    ],
)
def test_development_routing(question, route):
    assert UnderstandingEngine().route(question, {"metrics": []})["route"] == route
    if route == "KNOWLEDGE":
        assert not everyday_request(question, context_refs=[])


@pytest.mark.parametrize(
    "previous",
    [
        "根据公司制度，差旅报销上限是多少？",
        "查阅代码评审规范，只回答权限审批要求。",
        "读取仓库代码中的登录调用链，不修改代码。",
        "分析发布后的 p99 异常，使用只读日志。",
        "查看生产数据库的访问审批要求。",
    ],
)
@pytest.mark.parametrize(
    "followup",
    [
        "请再详细解释一下",
        "再短一点",
        "请举一个例子",
        "继续",
        "请展开说说",
        "用表格说明",
        "那为什么？",
        "有哪些例外？",
        "有什么限制？",
        "请总结一下",
        "帮我解释一下",
        "请补充一下",
        "详细一点",
        "这个结论怎么得出的？",
        "再给几个示例",
        "换成英文回答",
    ],
)
def test_development_followup_preserves_authorized_goal(previous, followup):
    effective = task_question(followup, previous)
    assert previous in effective
    assert followup in effective
    assert not everyday_request(followup, context_refs=[], previous_route="KNOWLEDGE")
    engine = UnderstandingEngine()
    assert (
        engine.route(effective, {"metrics": []})["route"]
        == engine.route(previous, {"metrics": []})["route"]
    )


@pytest.mark.parametrize(
    "question",
    [
        "请解释什么是机器学习",
        "请翻译：明天见。",
        "为什么天空是蓝色？",
        "帮我写一个排序函数示例",
        "请介绍一下你",
    ],
)
def test_new_topic_does_not_inherit_enterprise_goal(question):
    assert task_question(question, "根据公司制度查询报销上限") == question
    assert everyday_request(question, context_refs=[], previous_route="KNOWLEDGE")


def test_no_authorized_previous_goal_is_not_invented():
    assert task_question("请再详细解释一下", None) == "请再详细解释一下"
    assert not everyday_request("请再详细解释一下", context_refs=[])


def test_multiple_followups_keep_bounded_original_goal():
    question = "根据公司制度查询报销上限，仅使用授权资料。"
    for _ in range(30):
        question = task_question("请举一个例子", question)
    assert question.count("本轮追问:") == 1
    assert "仅使用授权资料" in question


def test_evidence_budget_preserves_target_table_and_complete_code():
    table = "| 地区 | 报销上限 |\n| --- | --- |\n| 上海 | 800元 |\n| 北京 | 600元 |"
    code = "```python\nif total > 800:\n    reject()\n```"
    evidence = json.dumps(
        [
            {
                "id": "ev-1",
                "type": "DOCUMENT",
                "content": {
                    "bodies": [
                        {
                            "body_index": 0,
                            "title": "差旅制度",
                            "text": "无关介绍" * 400 + "\n\n" + table + "\n\n" + code,
                        }
                    ]
                },
            }
        ],
        ensure_ascii=False,
    )
    pack = ContextBuilder(character_budget=650).pack(
        [
            ContextSegment(TrustLevel.USER, "上海报销上限以及 total 检查代码", "current-user", 900),
            ContextSegment(TrustLevel.UNTRUSTED_DATA, evidence, "evidence-bus", 800),
        ]
    )
    text = pack.messages[-1]["content"]
    assert table.replace("\n", r"\n") in text
    assert code.replace("\n", r"\n") in text
    assert "ev-1" in text and "body_index" in text
    assert "omitted_blocks" in text
    assert pack.used <= 650


@pytest.mark.parametrize("budget", [1, 2, 20, 24, 40, 72, 120, 300])
def test_oversized_structured_evidence_never_becomes_broken_json(budget):
    evidence = json.dumps([{"id": "ev-1", "content": "正文" * 1000}])
    result = summarize_segment(evidence, budget)
    assert len(result) <= budget
    if result:
        payload = json.loads(result)
        assert payload.get("incomplete", True)
        assert not payload.get("items")


def test_offline_subset_cannot_claim_answer_quality_pass():
    root = Path(__file__).resolve().parents[3]
    report = execute_offline_evaluations(root / "evaluations/datasets")
    assert report["status"] == "PASSED"  # Existing, explicitly scoped contract.
    assert report["scope"] == "OFFLINE_ROUTING_AND_SQL_POLICY"
    assert report["acceptance_status"] == "BLOCKED"
    assert report["answer_quality_status"] == "NOT_RUN"
    assert report["quality_eligible"] is False
    assert report["not_run"] == report["skipped"] == 27
    assert sum(item["status"] == "NOT_RUN" for item in report["results"]) == 27
    assert len(report["results"]) == report["cases"]


def test_real_task_entry_restores_topic_for_search_author_and_reviewer(client, monkeypatch):
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy import select
    from test_phase13_knowledge_agent import _create_thread, _wait_terminal

    from obsion.db.models import Evidence
    from obsion.model_gateway.gateway import ModelGateway, ModelResult

    fact = "青禾项目代码评审必须由两名维护者批准。"
    observed = []

    async def complete(self, session, **kwargs):
        sources = list(
            await session.scalars(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        )
        source = next(item for item in sources if fact in str(item.content))
        observed.append(str(kwargs["messages"]))
        if kwargs["step_id"] is None:
            payload = {
                "answerable": True,
                "answer": fact,
                "claims": [{"statement": fact, "evidence_ids": [str(source.id)]}],
            }
        else:
            payload = {
                "answer_supported": True,
                "question_answered": True,
                "claims": [
                    {
                        "claim_index": 1,
                        "verdict": "SUPPORTED",
                        "quotes": [{"evidence_id": str(source.id), "quote": fact}],
                    }
                ],
            }
        return ModelResult(
            content=json.dumps(payload, ensure_ascii=False),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            cost_amount=Decimal("0"),
            finish_reason="stop",
        )

    monkeypatch.setattr(ModelGateway, "complete", complete)
    response = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": ("review.md", ("# 青禾代码评审规范\n" + fact).encode(), "text/markdown"),
        },
        data={
            "source": "p1-development",
            "external_id": "p1-review-policy",
            "title": "青禾代码评审规范",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert response.status_code == 201, response.text
    thread = _create_thread(client)
    first_question = "根据青禾项目代码评审规范，评审需要多少名维护者批准？"
    normalized_question = unicodedata.normalize("NFKC", first_question)
    for question in (first_question, "请再详细解释一下", "请举一个例子"):
        response = client.post(f"/api/v1/threads/{thread['id']}/turns", json={"input": question})
        assert response.status_code == 202, response.text
        run = _wait_terminal(client, response.json()["run"]["id"])
        assert run["status"] == "COMPLETED", run
        assert run["intent"]["route"] == "KNOWLEDGE"
        assert normalized_question in run["intent"]["question"]
        steps = client.get(f"/api/v1/runs/{run['id']}/steps").json()
        search = next(item for item in steps if item["kind"] == "CAPABILITY")
        assert search["status"] == "COMPLETED"
        assert normalized_question in run["plan"]["steps"][0]["payload"]["query"]
        artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
        assert any(fact in item["inline_content"].get("markdown", "") for item in artifacts)
    assert len(observed) == 6
    assert all(normalized_question in messages for messages in observed)


def test_real_followup_reuses_selected_repository_in_each_planned_read(client):
    from test_phase13_knowledge_agent import _create_thread
    from test_phase21_code_graph import _index_sample, _wait_terminal

    # These are synthetic, locally indexed fixtures. No Codeup source is run.
    _index_sample(client, name="selected-api")
    _index_sample(client, name="other-api")
    thread = _create_thread(client)
    for index, question in enumerate(
        (
            "查询 selected-api 或 other-api 创建订单的代码调用链",
            "请再详细解释一下",
            "继续",
        )
    ):
        payload = {"input": question}
        if index == 0:
            payload["context_refs"] = [{"type": "repository", "value": "selected-api"}]
        response = client.post(f"/api/v1/threads/{thread['id']}/turns", json=payload)
        assert response.status_code == 202, response.text
        run = _wait_terminal(client, response.json()["run"]["id"])
        assert run["status"] == "COMPLETED", run
        assert run["intent"]["route"] == "ENGINEERING"
        assert run["plan"]["steps"]
        assert all(step["payload"]["repository"] == "selected-api" for step in run["plan"]["steps"])
