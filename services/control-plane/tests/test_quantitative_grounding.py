"""Linked documents must not authorize invented monetary or percentage values."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_critic import evidence
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.domain.enums import EvidenceType
from obsion.harness.critic import Critic
from obsion.harness.runtime import HarnessRuntime


@pytest.mark.parametrize(
    ("source", "statement"),
    [
        ("交通报销上限为500元。", "交通报销上限为1000000元。"),
        ("报销比例为50%。", "报销比例为80%。"),
        ("Expense limit is USD 500.", "Expense limit is EUR 500."),
        ("报销上限为500元。", "报销上限为500万元。"),
        ("Expense change is -5%.", "Expense change is 5%."),
        ("报销上限尚未确定。", "报销上限为500元。"),
    ],
)
def test_knowledge_claim_rejects_quantity_absent_from_its_linked_document(
    source: str, statement: str
) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"hits": [{"content": source, "title": statement}]}
    result = Critic().verify(
        [item],
        required_types=("DOCUMENT",),
        claims=[{"statement": statement, "evidence_ids": [str(item.id)]}],
        route="KNOWLEDGE",
        answer=statement,
    )
    assert not result.verified
    assert any("quantity_not_grounded" in c.get("reason_codes", []) for c in result.conflicts)


def test_other_evidence_and_correct_detached_claim_cannot_rescue_wrong_answer() -> None:
    linked = evidence(EvidenceType.DOCUMENT, "knowledge", "linked")
    linked.content = {"hits": [{"content": "报销上限500元。"}]}
    unlinked = evidence(EvidenceType.DOCUMENT, "knowledge", "unlinked")
    unlinked.content = {"hits": [{"content": "报销上限1000000元。"}]}
    result = Critic().verify(
        [linked, unlinked],
        required_types=("DOCUMENT",),
        claims=[{"statement": "报销上限500元。", "evidence_ids": [str(linked.id)]}],
        route="KNOWLEDGE",
        answer="报销上限1000000元。",
    )
    assert not result.verified
    assert any("quantity_not_grounded" in c.get("reason_codes", []) for c in result.conflicts)


def test_each_claim_uses_its_own_links_even_if_another_claim_has_the_amount() -> None:
    first = evidence(EvidenceType.DOCUMENT, "knowledge", "first")
    first.content = {"hits": [{"content": "上限500元。"}]}
    second = evidence(EvidenceType.DOCUMENT, "knowledge", "second")
    second.content = {"text": "上限1000元。"}
    result = Critic().verify(
        [first, second],
        required_types=("DOCUMENT",),
        claims=[
            {"statement": "第一项上限1000元。", "evidence_ids": [str(first.id)]},
            {"statement": "第二项上限1000元。", "evidence_ids": [str(second.id)]},
        ],
        route="KNOWLEDGE",
        answer="两项上限均为1000元。",
    )
    assert not result.verified
    assert any("quantity_not_grounded" in c.get("reason_codes", []) for c in result.conflicts)


def test_general_answer_does_not_require_enterprise_quantity_evidence() -> None:
    result = Critic().verify(
        [], required_types=(), claims=[], route="GENERAL", answer="50%的100元是50元。"
    )
    assert not result.verified
    assert result.conflicts == ()
    assert result.checks["general_response_scope"]


@pytest.mark.parametrize(
    ("source", "answer"),
    [
        ("报销上限500元。", "报销上限为人民币 500.00 元。"),
        ("报销上限为1万元。", "报销上限为10,000元。"),
        ("Expense limit is $1,000.", "Expense limit is USD 1000.00."),
        ("报销比例为50%。", "报销比例为５０％。"),
        ("Expense rate is 50 percent.", "Expense rate is 50%."),
        ("报销金额变化-500元。", "报销金额变化为-500.00元。"),
        ("审批分为两个步骤。", "审批共有2个步骤。"),
    ],
)
def test_supported_equivalent_quantities_and_non_quantitative_answers_remain_usable(
    source: str, answer: str
) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"text": source}
    result = Critic().verify(
        [item],
        required_types=("DOCUMENT",),
        claims=[{"statement": answer, "evidence_ids": [str(item.id)]}],
        route="KNOWLEDGE",
        answer=answer,
    )
    assert result.verified


def test_invented_money_is_withheld_from_every_published_answer_projection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("expense.md", "交通报销上限为500元。".encode(), "text/markdown")},
        data={
            "source": "quantity-test",
            "external_id": "expense-policy",
            "title": "交通报销制度",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text

    async def synthesize(self: HarnessRuntime, *args: Any) -> tuple[str, list[dict[str, Any]]]:
        records = args[5]
        assert records and records[0].content["hits"]
        return "交通报销上限为1000000元。", [
            {
                "statement": "交通报销上限为1000000元。",
                "evidence_ids": [str(records[0].id)],
                "confidence": 0.99,
            }
        ]

    monkeypatch.setattr(HarnessRuntime, "_synthesize", synthesize)
    thread = _create_thread(client)
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "交通报销上限是多少？"}
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(a for a in artifacts if a["title"] == "Obsion answer")["inline_content"]
    assert answer["verification"]["verified"] is False
    assert answer["markdown"].startswith("不知道：")
    assert answer["citations"] == []
    for artifact in artifacts:
        assert "1000000元" not in str(artifact["inline_content"].get("markdown", ""))
    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert [e["payload"]["delta"] for e in events if e["name"] == "answer.delta"] == [
        answer["markdown"]
    ]
