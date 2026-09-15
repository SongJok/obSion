"""Synthetic protocol/security tests, never evidence of real answer quality."""

import asyncio
import copy
import hashlib
import json
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from test_phase6_model_gateway import _completion
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.api.evaluations import get_answer_scoring_service
from obsion.application.answer_scoring import AnswerScoringService
from obsion.db.models import (
    AuditRecord,
    Document,
    Evidence,
    ModelCall,
    Organization,
    Policy,
    Run,
    User,
)
from obsion.domain.enums import Classification, DecisionEffect
from obsion.evaluations.acceptance import AcceptanceRunner, FrozenCase
from obsion.evaluations.semantic import POLICY, POLICY_SHA256, SemanticScoreRequest, semantic_score
from obsion.knowledge.service import KnowledgeService
from obsion.model_gateway.gateway import ModelGateway, ModelResult

SOURCE = "申请人提供合法发票，主管审核业务真实性。该制度没有规定住宿报销金额。"
ANSWER = "申请人需要提供合法发票，主管负责审核业务真实性。"
QUESTION = "根据财务报销制度，申请人需要提供什么材料？"


def frozen_case(document_id=None):
    return FrozenCase(
        id="synthetic-only",
        required=True,
        question=QUESTION,
        source_document_id=document_id or uuid4(),
        source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
        expected_kind="ANSWER",
        reviewed_source_quotes=[SOURCE],
        scoring_rules=["说明发票要求和审核责任，不增加无据条件"],
    )


def judgment(answer=ANSWER):
    return {
        "factual_correctness": True,
        "task_completed": True,
        "rules": [
            {
                "rule_index": 1,
                "passed": True,
                "reason": "The source supports the answer.",
                "answer_quotes": [answer],
                "source_quotes": [SOURCE],
            }
        ],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "bool_index",
        "extra",
        "fake_source",
        "fake_answer",
        "short_quote",
        "no_quote",
        "false_string",
    ],
)
def test_invalid_judge_output_cannot_pass(mutation):
    result = judgment()
    rule = result["rules"][0]
    if mutation == "missing":
        result["rules"] = []
    elif mutation == "duplicate":
        result["rules"].append(copy.deepcopy(rule))
    elif mutation == "bool_index":
        rule["rule_index"] = True
    elif mutation == "extra":
        result["verified"] = True
    elif mutation == "fake_source":
        rule["source_quotes"] = ["所有申请均自动获批。"]
    elif mutation == "fake_answer":
        rule["answer_quotes"] = ["系统自己说验证通过。"]
    elif mutation == "short_quote":
        rule["answer_quotes"] = ["申请"]
    elif mutation == "no_quote":
        rule["source_quotes"] = []
    else:
        result["task_completed"] = "false"
    with pytest.raises(ValueError):
        semantic_score(
            json.dumps(result),
            case=frozen_case(),
            answer=ANSWER,
            source=SOURCE,
            scorer_id="synthetic",
        )


@pytest.mark.parametrize(
    "kind", ["wrong_fact", "false_refusal", "failed_rule", "correct", "correct_insufficient"]
)
def test_semantic_verdicts_keep_false_refusals_and_missing_facts_separate(kind):
    case = frozen_case()
    answer = ANSWER
    if kind == "correct_insufficient":
        case = case.model_copy(update={"expected_kind": "INSUFFICIENT"})
        answer = "资料没有说明住宿报销金额，需要补充住宿费用标准。"
    result = judgment(answer)
    if kind == "wrong_fact":
        result["factual_correctness"] = False
    if kind == "false_refusal":
        result["task_completed"] = False
    if kind == "failed_rule":
        result["rules"][0]["passed"] = False
    score = semantic_score(
        json.dumps(result), case=case, answer=answer, source=SOURCE, scorer_id="synthetic"
    )
    assert score.status == ("PASS" if kind.startswith("correct") else "FAIL")
    assert score.answer_sha256 == hashlib.sha256(answer.encode()).hexdigest()
    assert SOURCE not in json.dumps(score.model_dump(), ensure_ascii=False)


@pytest.fixture
def published(client, monkeypatch):
    original_complete = ModelGateway.complete

    async def synthetic_author(self, session, **kwargs):
        if kwargs["run_id"] is None:
            return await original_complete(self, session, **kwargs)
        sources = list(
            await session.scalars(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        )
        source = next(item for item in sources if SOURCE in str(item.content))
        payload = (
            {
                "answerable": True,
                "answer": ANSWER,
                "claims": [{"statement": ANSWER, "evidence_ids": [str(source.id)]}],
            }
            if kwargs["step_id"] is None
            else {
                "answer_supported": True,
                "question_answered": True,
                "claims": [
                    {
                        "claim_index": 1,
                        "verdict": "SUPPORTED",
                        "quotes": [{"evidence_id": str(source.id), "quote": SOURCE}],
                    }
                ],
            }
        )
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

    monkeypatch.setattr(ModelGateway, "complete", synthetic_author)
    uploaded = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("finance.md", SOURCE.encode(), "text/markdown")},
        data={
            "source": "synthetic-scoring",
            "external_id": "finance",
            "title": "财务报销制度",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["document"]["id"]
    thread = _create_thread(client)
    created = client.post(f"/api/v1/threads/{thread['id']}/turns", json={"input": QUESTION})
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED"
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    artifact = next(item for item in artifacts if item["kind"] == "TEXT")
    assert artifact["inline_content"]["verification"]["verified"] is True
    profile = client.post(
        "/api/v1/admin/models/profiles",
        json={
            "name": "synthetic-judge",
            "requirements": {"capabilities": ["chat", "json_mode"]},
            "routing_policy": {"fallback": False},
            "enabled": True,
        },
    )
    assert profile.status_code == 201, profile.text
    endpoint = client.post(
        "/api/v1/admin/models/endpoints",
        json={
            "name": "synthetic-judge",
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model_id": "test-judge",
            "classifications": ["INTERNAL"],
            "capabilities": ["chat", "json_mode"],
            "limits": {"context_window": 32000, "max_output_tokens": 4000},
            "enabled": True,
        },
    )
    assert endpoint.status_code == 201, endpoint.text
    bound = client.post(
        f"/api/v1/admin/models/profiles/{profile.json()['id']}/endpoints",
        json={"endpoint_id": endpoint.json()["id"], "priority": 1},
    )
    assert bound.status_code == 201, bound.text
    case = frozen_case(UUID(document_id))
    answer = artifact["inline_content"]["markdown"]
    request = SemanticScoreRequest(
        run_id=run["id"],
        case=case,
        answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
        model_profile_id=profile.json()["id"],
        policy_sha256=POLICY_SHA256,
    )
    return request, answer, run


@pytest.mark.parametrize(
    "mode",
    [
        "pass",
        "semantic_failure",
        "truncated",
        "invalid",
        "unavailable",
        "timeout",
        "wrong_answer",
        "wrong_question",
        "wrong_source",
        "wrong_policy",
        "unknown_run",
        "denied",
        "foreign_org",
        "workspace_denied",
        "source_revoked_during_model",
        "classification_raised_during_model",
        "policy_revoked_during_model",
    ],
)
def test_normal_task_is_scored_through_api_gateway_policy_and_immutable_audit(
    client, published, mode
):
    request, answer, original_run = published
    observed = []
    service = None

    async def provider(http_request):
        body = json.loads(http_request.content)
        observed.append(body)
        assert body["messages"][0]["content"] == POLICY
        data = json.loads(body["messages"][1]["content"])
        assert data["source"] == SOURCE and data["answer"] == answer
        assert "verified" not in data and "claims" not in data and "tools" not in body
        if mode == "unavailable":
            return httpx.Response(503)
        if mode == "timeout":
            await asyncio.sleep(0.1)
        if mode == "source_revoked_during_model":
            # Same-transaction mutation probes the service's mandatory re-read;
            # cross-transaction PostgreSQL revocation is tested separately.
            document = await service.active_session.get(Document, request.case.source_document_id)
            document.acl = {"deny_users": [str(service.principal_id)]}
            await service.active_session.flush()
        if mode == "classification_raised_during_model":
            document = await service.active_session.get(Document, request.case.source_document_id)
            document.classification = Classification.RESTRICTED
            await service.active_session.flush()
        if mode == "policy_revoked_during_model":
            service.active_session.add(
                Policy(
                    organization_id=service.organization_id,
                    name="deny-scoring-after",
                    version=1,
                    priority=1000,
                    effect=DecisionEffect.DENY,
                    conditions={"actions": ["evaluations.write"]},
                    obligations=[],
                    reason="revoked in fixture",
                    created_by=service.principal_id,
                    enabled=True,
                )
            )
            await service.active_session.flush()
        output = judgment(answer)
        if mode == "semantic_failure":
            output["task_completed"] = False
        if mode == "invalid":
            output["rules"] = []
        response = _completion(json.dumps(output), input_tokens=20, output_tokens=30)
        if mode == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=response)

    class ObservedScoringService(AnswerScoringService):
        async def score(self, session, principal, req):
            self.active_session = session
            self.organization_id = principal.organization_id
            self.principal_id = principal.id
            if mode == "denied":
                from dataclasses import replace

                principal = replace(principal, permissions=frozenset())
            if mode in {"foreign_org", "workspace_denied"}:
                from dataclasses import replace

                organization_id = principal.organization_id
                if mode == "foreign_org":
                    organization_id = uuid4()
                    session.add(
                        Organization(
                            id=organization_id,
                            slug=f"scoring-{organization_id}",
                            name="Other test organization",
                            active=True,
                            settings={},
                        )
                    )
                    await session.flush()
                user_id = uuid4()
                session.add(
                    User(
                        id=user_id,
                        organization_id=organization_id,
                        external_id=f"scoring-{user_id}",
                        email=f"{user_id}@example.invalid",
                        display_name="Nonmember",
                        active=True,
                        attributes={},
                    )
                )
                await session.flush()
                principal = replace(
                    principal,
                    id=user_id,
                    organization_id=organization_id,
                    roles=frozenset(),
                    permissions=frozenset({"evaluations.write"}),
                )
            return await super().score(session, principal, req)

    settings = client.app.state.settings
    service = ObservedScoringService(
        KnowledgeService(settings, client.app.state.object_store),
        ModelGateway(settings, transport=httpx.MockTransport(provider)),
    )
    client.app.dependency_overrides[get_answer_scoring_service] = lambda: service
    body = request.model_dump(mode="json")
    if mode == "wrong_answer":
        body["answer_sha256"] = "0" * 64
    if mode == "wrong_question":
        body["case"]["question"] = "Another question"
    if mode == "wrong_source":
        body["case"]["source_sha256"] = "0" * 64
    if mode == "wrong_policy":
        body["policy_sha256"] = "0" * 64
    if mode == "unknown_run":
        body["run_id"] = str(uuid4())
    if mode == "timeout":
        # Force the real deadline mechanism rather than wait 45s in a test.
        original_timeout = asyncio.timeout
        from unittest.mock import patch

        with patch(
            "obsion.model_gateway.gateway.asyncio.timeout", lambda _: original_timeout(0.01)
        ):
            response = client.post("/api/v1/admin/evaluations/answer-score", json=body)
    else:
        response = client.post("/api/v1/admin/evaluations/answer-score", json=body)
    assert response.status_code == 200, response.text
    score = response.json()
    assert score["status"] == (
        "PASS" if mode == "pass" else "FAIL" if mode == "semantic_failure" else "BLOCKED"
    ), score
    should_call = mode not in {
        "wrong_answer",
        "wrong_question",
        "wrong_source",
        "wrong_policy",
        "unknown_run",
        "denied",
        "foreign_org",
        "workspace_denied",
    }
    assert len(observed) == int(should_call)

    async def records():
        async with client.app.state.database.sessions() as session:
            audit = await session.get(AuditRecord, UUID(score["evidence"][-1]["audit_id"]))
            calls = list(
                await session.scalars(
                    select(ModelCall).where(ModelCall.profile_id == request.model_profile_id)
                )
            )
            run = await session.get(Run, request.run_id)
            return (
                audit,
                calls,
                (run.status.value, run.input_tokens, run.output_tokens, str(run.cost_amount)),
            )

    audit, calls, after = client.portal.call(records)
    assert audit.outcome == score["status"] and audit.policy_decision_id
    assert len(calls) == int(should_call)
    assert all(call.run_id is None for call in calls)
    assert score["evidence"][-1]["model_call_ids"] == [str(call.id) for call in calls]
    if mode == "pass":
        assert score["evidence"][-1]["model_call_id"] == str(calls[0].id)
    if mode == "timeout":
        assert calls[0].outcome == "TIMEOUT"
    assert after[:3] == (
        original_run["status"],
        original_run["input_tokens"],
        original_run["output_tokens"],
    )
    assert Decimal(after[3]) == Decimal(original_run["cost_amount"])


def test_frozen_runner_resolves_scorer_and_records_real_api_result(
    client, published, app_settings, monkeypatch, tmp_path
):
    request, _, original_run = published
    revision, image_digest = "a" * 40, "sha256:" + "b" * 64
    monkeypatch.setattr(app_settings, "release_revision", revision)
    monkeypatch.setattr(app_settings, "release_image_digest", image_digest)

    def provider(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json=_completion(json.dumps(judgment(data["answer"]))))

    service = AnswerScoringService(
        KnowledgeService(app_settings, client.app.state.object_store),
        ModelGateway(app_settings, transport=httpx.MockTransport(provider)),
    )
    client.app.dependency_overrides[get_answer_scoring_service] = lambda: service
    raw = json.dumps(
        {
            "phase": "P1",
            "split": "held_out",
            "sources": [
                {
                    "document_id": str(request.case.source_document_id),
                    "sha256": request.case.source_sha256,
                }
            ],
            "cases": [request.case.model_dump(mode="json")],
        },
        ensure_ascii=False,
    ).encode()
    (tmp_path / "heldout.json").write_bytes(raw)
    workspace = client.post(
        "/api/v1/workspaces", json={"name": "Independent scoring contract"}
    ).json()
    candidate_profile = next(
        item
        for item in client.get("/api/v1/admin/models/profiles").json()
        if item["id"] == original_run["model_profile_id"]
    )
    sent_turns = []

    def transport(request):
        if request.method == "POST" and request.url.path.endswith("/turns"):
            sent_turns.append(json.loads(request.content))
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={"Content-Type": "application/json"},
        )
        return httpx.Response(response.status_code, content=response.content)

    async def execute():
        async with httpx.AsyncClient(
            base_url="http://localhost", transport=httpx.MockTransport(transport)
        ) as api:
            profile = await AcceptanceRunner.freeze(
                api,
                tmp_path,
                name="synthetic-scoring",
                candidate=revision,
                image_digest=image_digest,
                workspace_id=UUID(workspace["id"]),
                model_profile=candidate_profile["name"],
                dataset="heldout.json",
                dataset_sha256=hashlib.sha256(raw).hexdigest(),
            )
            runner = AcceptanceRunner(
                api, profile, candidate=revision, scorer_model_profile="synthetic-judge"
            )
            report = await runner.run(tmp_path, tmp_path / "scored")
            missing = AcceptanceRunner(
                api, profile, candidate=revision, scorer_model_profile="missing-judge"
            )
            blocked = await missing.run(tmp_path, tmp_path / "blocked")
            return report, blocked

    report, blocked = asyncio.run(execute())
    assert report["counts"] == {"PASS": 1, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 0}, report
    assert report["scorer"]["profile_id"] == str(request.model_profile_id)
    assert report["scorer"]["policy_sha256"] == POLICY_SHA256
    assert report["phase_status"] == "BLOCKED" and not report["promotion_eligible"]
    assert report["results"][0]["score"]["evidence"][-1]["model_call_ids"]
    assert (
        blocked["status"] == "BLOCKED"
        and blocked["blocker"] == "independent_model_profile_unavailable"
    )
    assert sent_turns == [{"input": QUESTION, "model_profile": candidate_profile["name"]}]
    assert (tmp_path / "blocked/report.json").is_file()
