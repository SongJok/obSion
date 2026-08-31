import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsion.harness.agent_router import AgentRouter
from obsion.harness.planner import Planner
from obsion.harness.understanding import UnderstandingEngine
from obsion.registry.manifests import load_registry_specs

_WRITE_CAPABILITIES = {
    "action.ticket.create",
    "action.ticket.close",
    "action.ticket.rollback",
    "action.pr.create",
    "data.mutate",
    "k8s.restart",
    "k8s.scale",
    "knowledge.ingest",
    "knowledge.sync",
}


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def _create_thread(client: TestClient, title: str) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": title, "description": "Phase 24 specialist routing"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": title},
    )
    assert thread.status_code == 201, thread.text
    return thread.json()


def _empty_understanding(question: str) -> dict:
    return {
        "question": question,
        "domain": "KNOWLEDGE",
        "intent": "ANALYTICS_QUERY",
        "metrics": [],
        "dimensions": [],
        "time_range": {},
        "comparison": None,
    }


def test_phase24_skills_are_declared_with_evidence_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_REGISTRY_ROOT", str(Path(__file__).resolve().parents[3]))
    _, skills = load_registry_specs({}, {})
    required = {
        "sql-analysis",
        "business-analysis",
        "trend-analysis",
        "funnel-analysis",
        "code-review",
        "log-analysis",
        "root-cause-analysis",
        "report-generation",
        "support-diagnosis",
    }
    assert required <= set(skills)
    for name in required:
        spec = skills[name]
        assert spec["instructions"]
        assert spec["capabilities"]
        assert spec["requiredEvidence"]
        assert spec["verification"]
        assert not _WRITE_CAPABILITIES.intersection(spec["capabilities"])


def test_understanding_routes_support_and_operation_without_stealing_knowledge() -> None:
    engine = UnderstandingEngine()
    support = engine.route(
        "用户投诉无法退款，客服工单里应该怎么处理？",
        _empty_understanding("用户投诉无法退款，客服工单里应该怎么处理？"),
    )
    operation = engine.route(
        "checkout 工作负载当前 k8s 副本是否就绪？",
        _empty_understanding("checkout 工作负载当前 k8s 副本是否就绪？"),
    )
    knowledge = engine.route(
        "公司的差旅报销政策是什么？",
        _empty_understanding("公司的差旅报销政策是什么？"),
    )
    analytics = engine.route(
        "付费人数的漏斗转化率最近怎么样？",
        {
            **_empty_understanding("付费人数的漏斗转化率最近怎么样？"),
            "metrics": [{"id": "paid_user_count"}],
        },
    )
    decline = engine.route(
        "为什么付费人数下降？",
        {
            **_empty_understanding("为什么付费人数下降？"),
            "metrics": [{"id": "paid_user_count"}],
        },
    )

    assert support["route"] == "SUPPORT"
    assert support["intent"] == "SUPPORT"
    assert operation["route"] == "OPERATION"
    assert knowledge["route"] == "KNOWLEDGE"
    assert analytics["route"] == "ANALYTICS"
    assert decline["route"] == "DATA"


def test_specialist_router_pins_support_operation_and_review_skills() -> None:
    assert AgentRouter._SPECIALISTS["SUPPORT"] == ("support-agent", "support-diagnosis")
    assert AgentRouter._SPECIALISTS["OPERATION"] == ("operation-agent", "log-analysis")
    assert AgentRouter._SPECIALISTS["ANALYTICS"] == ("analytics-agent", "business-analysis")
    assert AgentRouter._SPECIALISTS["INCIDENT"] == ("incident-agent", "incident-investigation")
    assert (
        AgentRouter._pin_skill("ENGINEERING", "请评审这段代码的调用链", "code-architecture")
        == "code-review"
    )
    assert (
        AgentRouter._pin_skill("ANALYTICS", "付费人数的漏斗转化率最近怎么样？", "business-analysis")
        == "funnel-analysis"
    )
    assert AgentRouter._pin_skill("DATA", "生成付费人数的 SQL", "governed-analytics") == (
        "sql-analysis"
    )


def test_support_plan_is_read_only_tickets_and_knowledge() -> None:
    plan = Planner().create(
        {
            "route": "SUPPORT",
            "question": "用户投诉无法退款，客服工单里应该怎么处理？",
        },
        available_capabilities=frozenset(
            {
                "ticket.search",
                "knowledge.search",
                "document.read",
                "log.search",
                "trace.search",
                "k8s.status",
                "data.query",
                "action.ticket.create",
            }
        ),
    )
    selected = [step.capability for step in plan.steps]
    assert selected == ["ticket.search", "knowledge.search"]
    assert plan.required_evidence == ("DOCUMENT",)
    assert "no_write_path" in plan.verification
    assert not _WRITE_CAPABILITIES.intersection(selected)
    assert "k8s.status" not in selected
    assert "data.query" not in selected
    assert plan.steps[0].payload["operation"] == "ticket.search"
    assert plan.steps[0].environment == "development"


def test_operation_plan_is_read_only_status_and_never_writes() -> None:
    plan = Planner().create(
        {
            "route": "OPERATION",
            "question": "checkout 工作负载当前 k8s 副本是否就绪？",
            "service": "checkout",
            "time_range": {},
        },
        available_capabilities=frozenset(
            {"k8s.status", "deployment.list", "config.get", "log.search", "action.pr.create"}
        ),
    )
    selected = [step.capability for step in plan.steps]
    assert selected == ["k8s.status", "deployment.list", "config.get", "log.search"]
    assert "action.pr.create" not in selected
    assert not _WRITE_CAPABILITIES.intersection(selected)
    assert "no_write_path" in plan.verification
    assert "TOOL" in plan.required_evidence


def test_support_diagnosis_e2e_pins_agent_and_cites_ticket_evidence(
    client: TestClient,
) -> None:
    ticket = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "refund-ticket.md",
                (
                    "# 工单 INC-2048 退款被拦截\n"
                    "用户投诉无法退款。客服必须遵循已授权退款政策，"
                    "不得在政策外发放现金。"
                ).encode(),
                "text/markdown",
            )
        },
        data={
            "source": "ticket",
            "external_id": "INC-2048",
            "title": "退款被拦截工单",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert ticket.status_code == 201, ticket.text
    policy = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "refund-policy.md",
                (
                    "# 退款政策\n"
                    "已授权退款必须走原支付渠道，并需要负责人批准的客服工单。"
                    "不得直接转账现金。"
                ).encode(),
                "text/markdown",
            )
        },
        data={
            "source": "support-policy",
            "external_id": "refund-policy",
            "title": "退款政策",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert policy.status_code == 201, policy.text

    thread = _create_thread(client, "Support diagnosis")
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "用户投诉无法退款，客服工单里应该怎么处理？"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])

    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    assert run["intent"]["route"] == "SUPPORT"
    assert run["intent"]["agent"] == "support-agent"
    assert run["intent"]["skill"] == "support-diagnosis"
    assert run["plan"]["agent"] == "support-agent"
    assert run["plan"]["skill"]["name"] == "support-diagnosis"
    assert run["plan"]["skill"]["required_evidence"] == ["DOCUMENT"]
    steps = client.get(f"/api/v1/runs/{run['id']}/steps").json()
    capability_steps = [item for item in steps if item["kind"] == "CAPABILITY"]
    assert [item["name"] for item in capability_steps] == [
        "Search authorized support tickets",
        "Search authorized support knowledge",
    ]
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(item["inline_content"] for item in artifacts if item["kind"] == "TEXT")
    assert answer["citations"]
    assert "### 引用" in answer["markdown"]
    claims = client.get(f"/api/v1/runs/{run['id']}/claims").json()
    assert claims and claims[0]["evidence_ids"]
