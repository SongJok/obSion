import json
import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_knowledge_grounding import SOURCE
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.db.models import AuditRecord, Evidence, RunStep
from obsion.domain.enums import Classification
from obsion.harness.investigation import INVESTIGATION_POLICY, action_fingerprint, validate_action
from obsion.harness.runtime import HarnessRuntime
from obsion.knowledge.publication import KnowledgePublicationGuard
from obsion.model_gateway.gateway import ModelGateway, ModelResult


@pytest.mark.parametrize(
    "value",
    [
        {"action": "READ", "document_ref": "foreign"},
        {"action": "READ", "document_ref": []},
        {"action": "SEARCH", "query": "x"},
        {"action": "SEARCH", "query": "x" * 513},
        {"action": "SEARCH", "query": True},
        {"action": "SEARCH", "query": "policy", "url": "https://example.com"},
        {"action": "WRITE", "query": "policy"},
        [],
    ],
)
def test_investigation_proposals_cannot_bypass_action_contract(value):
    assert (
        validate_action(value, targets={}, available={"knowledge.search"}, attempted=set()) is None
    )


def test_search_deduplication_and_capability_allowlist():
    previous = {
        "capability": "knowledge.search",
        "payload": {"query": "POLICY  limits", "limit": 8},
    }
    assert (
        validate_action(
            {"action": "SEARCH", "query": "policy limits"},
            targets={},
            available={"knowledge.search"},
            attempted={action_fingerprint(previous)},
        )
        is None
    )
    assert (
        validate_action(
            {"action": "READ", "document_ref": "d1"},
            targets={"d1": {"document_id": str(uuid4()), "version": 1, "offset": 0}},
            available={"knowledge.search"},
            attempted=set(),
        )
        is None
    )


@pytest.mark.parametrize(
    "mode",
    [
        "pages",
        "workspace",
        "search_then_read",
        "stop",
        "repeat",
        "limit",
        "contradicted",
        "resume",
        "unchanged",
        "access_changed",
        "truncated",
    ],
)
def test_durable_investigation_runs_real_governed_tools_and_full_review(client, monkeypatch, mode):
    private_workspace = mode == "workspace"
    if private_workspace:
        mode = "pages"
    # Synthetic document/model decisions; the native index, Gateway, Policy,
    # durable step insertion, source metadata and final review are exercised.
    body = (
        "\n\n".join(
            f"# 交通制度第{index}节\n" + ("归档说明仅记录材料目录，不说明审批条件。" * 55)
            for index in range(6)
        )
        + "\n\n# 审批规则\n"
        + SOURCE
    )
    upload = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("investigation.md", body.encode(), "text/markdown")},
        data={
            "source": "investigation-test",
            "external_id": "multi-page",
            "title": "交通审批制度",
            "classification": "INTERNAL" if private_workspace else "RESTRICTED",
            "acl": '{"organization":true}',
        },
    )
    assert upload.status_code == 201, upload.text
    authors, proposals, reviews = [], [], []
    denied = False
    original_guard = KnowledgePublicationGuard.check

    async def check_access(self, *args, **kwargs):
        if denied:
            return False
        return await original_guard(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgePublicationGuard, "check", check_access)
    client.app.state.settings.run_max_knowledge_investigation_rounds = 2

    async def complete(self, session, **kwargs):
        nonlocal denied
        assert kwargs["classification"] == Classification.RESTRICTED
        items = list(
            await session.scalars(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        )
        if kwargs["messages"][0]["content"] == INVESTIGATION_POLICY:
            context = json.loads(kwargs["messages"][1]["content"])
            proposals.append(context)
            if mode == "access_changed":
                denied = True
            assert context["readable_document_refs"]
            if mode == "stop":
                payload = {"action": "STOP"}
            elif mode == "repeat":
                payload = {"action": "SEARCH", "query": context["previous_queries"][0]}
            elif mode == "search_then_read" and len(proposals) == 1:
                payload = {"action": "SEARCH", "query": "先审批后报销"}
            else:
                payload = {"action": "READ", "document_ref": context["readable_document_refs"][0]}
        elif kwargs["step_id"] is not None:
            reviews.append(True)
            context = json.loads(kwargs["messages"][1]["content"])
            evidence_id = context["claims"][0]["evidence_ids"][0]
            source = next(x for x in items if str(x.id) == evidence_id)
            assert any(SOURCE in hit["content"] for hit in source.content["hits"])
            payload = {
                "answer_supported": mode not in {"contradicted", "unchanged"},
                "question_answered": True,
                "claims": [
                    {
                        "claim_index": 1,
                        "verdict": "SUPPORTED",
                        "quotes": [{"evidence_id": evidence_id, "quote": SOURCE}],
                    }
                ],
            }
        else:
            authors.append(True)
            if (len(proposals) < 2 and mode != "unchanged") or mode == "limit":
                payload = {
                    "answerable": False,
                    "answer": "资料不够",
                    "claims": [],
                    "missing_information": "审批",
                }
            else:
                source = next(
                    x
                    for x in reversed(items)
                    if any(SOURCE in h["content"] for h in x.content.get("hits", []))
                )
                payload = {
                    "answerable": True,
                    "answer": "交通报销须先审批后报销。",
                    "claims": [
                        {
                            "statement": "交通报销须先审批后报销。",
                            "evidence_ids": [str(source.id)],
                            "confidence": 0.9,
                        }
                    ],
                }
        return ModelResult(
            content=json.dumps(payload),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            cost_amount=Decimal("0.01"),
            latency_ms=1,
            finish_reason=(
                "length"
                if mode == "truncated" and kwargs["messages"][0]["content"] == INVESTIGATION_POLICY
                else "stop"
            ),
        )

    monkeypatch.setattr(ModelGateway, "complete", complete)
    paused = False
    waves = 0
    execute_steps = HarnessRuntime._execute_steps

    async def pause_after_committed_plan(self, *args):
        nonlocal paused, waves
        waves += 1
        if waves == 2:
            paused = True
            return False
        return await execute_steps(self, *args)

    if mode == "resume":
        monkeypatch.setattr(HarnessRuntime, "_execute_steps", pause_after_committed_plan)
    if private_workspace:
        workspace = client.post(
            "/api/v1/workspaces",
            json={
                "name": "Restricted investigation workspace",
                "classification": "RESTRICTED",
                "description": "Synthetic restricted workspace information used by the author",
            },
        )
        assert workspace.status_code == 201
        created_thread = client.post(
            "/api/v1/threads",
            json={
                "workspace_id": workspace.json()["id"],
                "title": "Investigation classification",
            },
        )
        assert created_thread.status_code == 201
        thread = created_thread.json()
    else:
        thread = _create_thread(client)
    response = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "根据交通制度，报销如何审批？"}
    )
    assert response.status_code == 202
    if mode == "resume":
        for _ in range(150):
            if paused:
                break
            time.sleep(0.05)
        assert paused
        saved = client.get(f"/api/v1/runs/{response.json()['run']['id']}").json()
        assert saved["status"] == "RUNNING"
        assert len(saved["plan"]["knowledge_investigation"]) == 1
        client.portal.call(
            client.app.state.run_worker.runtime.execute,
            client.app.state.settings.dev_organization_id,
            UUID(saved["id"]),
        )
    run = _wait_terminal(client, response.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run.get("error_message")
    denied = False
    run = client.get(f"/api/v1/runs/{run['id']}").json()
    steps = client.get(f"/api/v1/runs/{run['id']}/steps").json()
    capability_steps = [s for s in steps if s["kind"] == "CAPABILITY"]
    assert all(s["status"] == "COMPLETED" and s["capability_version_id"] for s in capability_steps)
    expected_rounds = (
        1 if mode in {"stop", "repeat", "unchanged", "access_changed", "truncated"} else 2
    )
    assert len(proposals) == expected_rounds
    assert len(capability_steps) == (
        1
        if mode in {"stop", "repeat", "access_changed", "truncated"}
        else 2
        if mode == "unchanged"
        else 3
    )
    assert [s["ordinal"] for s in steps] == list(range(1, len(steps) + 1))
    assert (
        run["input_tokens"]
        == run["output_tokens"]
        == 10 * (len(authors) + len(proposals) + len(reviews))
    )
    assert len(run["plan"]["knowledge_investigation"]) == expected_rounds
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(a["inline_content"] for a in artifacts if a["title"] == "Obsion answer")
    assert answer["verification"]["verified"] is (
        mode in {"pages", "search_then_read", "resume"}
    ), answer
    reflect = next(s for s in steps if s["kind"] == "REFLECT")
    assert (
        reflect["output_ref"]
        == (
            "reflect.RESPOND"
            if mode in {"pages", "search_then_read", "resume"}
            else "reflect.WITHHOLD"
        ).lower()
    )
    if mode in {"pages", "search_then_read", "resume"}:
        assert answer["citations"][0]["evidence_id"] != capability_steps[0]["output_ref"]
    if mode not in {"pages", "search_then_read", "resume"}:
        assert answer["markdown"].startswith("不知道：") and not answer["citations"]
    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert [e["payload"]["delta"] for e in events if e["name"] == "answer.delta"] == [
        answer["markdown"]
    ]
    if mode == "unchanged":
        assert len(reviews) == 1 and len(authors) == 2
        assert answer["verification"]["conflicts"][0]["reason_codes"] == [
            "unchanged_rejected_candidate"
        ]
    if mode in {"pages", "resume"}:

        async def check_pages():
            async with client.app.state.database.sessions() as session:
                audits = list(
                    await session.scalars(
                        select(AuditRecord).where(
                            AuditRecord.correlation_id == UUID(run["id"]),
                            AuditRecord.resource_type == "capability",
                            AuditRecord.outcome == "SUCCESS",
                            AuditRecord.action == "knowledge.read",
                        )
                    )
                )
                assert len(audits) == 3
                assert all(
                    a.redacted_metadata["result_classification"]
                    == (Classification.INTERNAL if private_workspace else Classification.RESTRICTED)
                    for a in audits
                )
                rows = list(
                    await session.scalars(
                        select(RunStep)
                        .where(RunStep.run_id == UUID(response.json()["run"]["id"]))
                        .order_by(RunStep.ordinal)
                    )
                )
                return [
                    r.input_payload["payload"]["offset"]
                    for r in rows
                    if r.input_payload.get("capability") == "document.read"
                ]

        assert client.portal.call(check_pages) == [0, 4]
