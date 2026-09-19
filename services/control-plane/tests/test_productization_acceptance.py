"""Adversarial driver tests; synthetic API/scoring is never live quality evidence."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from obsion.cli import build_parser
from obsion.evaluations.acceptance import (
    _CONFIG_PATHS,
    _CONFIG_PATHS_V2,
    AcceptanceError,
    AcceptanceProfile,
    AcceptanceRunner,
    Score,
    load_frozen_cases,
)
from obsion.evaluations.engine import canonical_sha256

REVISION = "a" * 40
IMAGE = "sha256:" + "b" * 64
SOURCE = "申请人提供合法发票。主管审核业务真实性。资料没有列出住宿金额。"
SOURCE_ID = str(UUID(int=1))
PROFILE_ID = str(UUID(int=2))
AGENT_ID = str(UUID(int=3))


@pytest.fixture
def frozen(tmp_path):
    digest = hashlib.sha256(SOURCE.encode()).hexdigest()
    cases = [
        {
            "id": f"case-{i}",
            "required": True,
            "question": f"制度问题{i}？",
            "source_document_id": SOURCE_ID,
            "source_sha256": digest,
            "expected_kind": "INSUFFICIENT" if i == 3 else "ANSWER",
            "reviewed_source_quotes": [SOURCE],
            "scoring_rules": ["independently_review_factual_correctness"],
        }
        for i in range(1, 4)
    ]
    document = {
        "phase": "P1",
        "split": "held_out",
        "sources": [{"document_id": SOURCE_ID, "sha256": digest}],
        "cases": cases,
    }
    raw = json.dumps(document, ensure_ascii=False).encode()
    (tmp_path / "heldout.json").write_bytes(raw)
    profile = AcceptanceProfile(
        schema_version=1,
        name="synthetic-driver-contract",
        phase="P1",
        api_base_url="http://localhost",
        workspace_id=UUID(int=4),
        dataset="heldout.json",
        dataset_sha256=hashlib.sha256(raw).hexdigest(),
        image_digest=IMAGE,
        model_profile="test-only",
        model_profile_id=UUID(PROFILE_ID),
        agent_version_id=UUID(AGENT_ID),
        configuration_sha256=dict.fromkeys(_CONFIG_PATHS, canonical_sha256([])),
        timeout_seconds=1,
        poll_seconds=0.01,
    )
    return tmp_path, profile


class SyntheticAPI:
    def __init__(self):
        self.requests = []
        self.turns = 0
        self.run_state = "COMPLETED"
        self.identity = {"revision": REVISION, "image_digest": IMAGE}
        self.drift = False
        self.source_denied = False
        self.postflight_revoke = False
        self.wrong_model = False
        self.ambiguous = False
        self.cancelled = []
        self.created_at = {}

    def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        if path.endswith("runtime-identity"):
            return httpx.Response(200, json=self.identity)
        if path in _CONFIG_PATHS:
            return httpx.Response(200, json=["changed"] if self.drift and self.turns else [])
        if path.endswith("/content"):
            denied = self.source_denied or (self.postflight_revoke and self.turns > 0)
            return httpx.Response(403 if denied else 200, content=SOURCE.encode())
        if path == "/api/v1/threads":
            return httpx.Response(201, json={"id": str(UUID(int=100 + self.turns))})
        if path.endswith("/turns"):
            if request.method == "GET":
                number = UUID(path.split("/")[-2]).int - 99
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": str(UUID(int=400 + number)),
                            "input_text": f"制度问题{number}？",
                            "context_refs": [],
                            "attachment_refs": [],
                        }
                    ],
                )
            self.turns += 1
            self.created_at[self.turns] = datetime.now(UTC).isoformat()
            return httpx.Response(202, json={"run": self.run()})
        if path.endswith("/threads") and request.method == "GET":
            return httpx.Response(
                200, json=[{"id": str(UUID(int=100 + i))} for i in range(self.turns)]
            )
        if path.startswith("/api/v1/threads/") and path.endswith("/runs"):
            number = UUID(path.split("/")[-2]).int - 99
            return httpx.Response(200, json=[self.run(number)])
        if path.endswith("/cancel"):
            self.cancelled.append(path)
            return httpx.Response(200, json={"status": "CANCELLED"})
        if path.endswith("/artifacts"):
            answer = {
                "id": str(UUID(int=UUID(path.split("/")[-2]).int + 100)),
                "kind": "TEXT",
                "inline_content": {"markdown": "这是一个错误答案。", "verified": True},
            }
            return httpx.Response(200, json=[answer, answer] if self.ambiguous else [answer])
        if path.endswith(("/steps", "/evidence")):
            return httpx.Response(200, json=[])
        if path.startswith("/api/v1/runs/"):
            return httpx.Response(200, json=self.run(UUID(path.split("/")[-1]).int - 200))
        raise AssertionError(f"Unexpected candidate API path: {path}")

    def run(self, number=None):
        number = self.turns if number is None else number
        return {
            "id": str(UUID(int=200 + number)),
            "turn_id": str(UUID(int=400 + number)),
            "created_at": self.created_at[number],
            "completed_at": self.created_at[number] if self.run_state == "COMPLETED" else None,
            "status": self.run_state,
            "model_profile_id": str(UUID(int=999)) if self.wrong_model else PROFILE_ID,
            "agent_version_id": AGENT_ID,
            "source_content_available": True,
        }


class SyntheticScorer:
    def __init__(self, *, wrong_digest=False, crash=False):
        self.observed = []
        self.wrong_digest = wrong_digest
        self.crash = crash

    async def score(self, **kwargs):
        self.observed.append(kwargs)
        if self.crash:
            raise RuntimeError("sensitive upstream failure that must not be exported")
        return Score(
            status="FAIL" if kwargs["case"].id == "case-2" else "PASS",
            reason="synthetic_scorer_test_only",
            scorer_id="explicit-test-adapter",
            answer_sha256=(
                "c" * 64
                if self.wrong_digest
                else hashlib.sha256(kwargs["answer"].encode()).hexdigest()
            ),
        )


async def execute(frozen, api, scorer=None):
    root, profile = frozen
    async with httpx.AsyncClient(
        base_url=profile.api_base_url, transport=httpx.MockTransport(api)
    ) as client:
        return await AcceptanceRunner(client, profile, candidate=REVISION, scorer=scorer).run(
            root, root / "result"
        )


async def test_normal_entry_never_receives_gold_and_denominator_keeps_failures(frozen):
    api, scorer = SyntheticAPI(), SyntheticScorer()
    report = await execute(frozen, api, scorer)
    assert api.turns == 3
    turns = [body for _, path, body in api.requests if path.endswith("/turns")]
    assert turns == [{"input": f"制度问题{i}？", "model_profile": "test-only"} for i in range(1, 4)]
    assert report["counts"] == {"PASS": 2, "FAIL": 1, "BLOCKED": 0, "NOT_RUN": 0}
    assert report["status"] == "FAIL"
    assert report["answerable_accuracy"] == 0.5
    assert report["insufficient_accuracy"] == 1.0
    assert report["phase_status"] == "BLOCKED"
    assert not report["promotion_eligible"]
    assert all(item["source"] == SOURCE for item in scorer.observed)
    assert all(item["answer"] == "这是一个错误答案。" for item in scorer.observed)
    for name in ("inputs.json", "profile.json", "report.json"):
        assert (frozen[0] / "result" / name).stat().st_mode & 0o777 == 0o600


def test_h01_profile_v2_freezes_candidate_packages_connectors_and_evaluation(frozen) -> None:
    _, legacy = frozen
    document = {
        **legacy.model_dump(mode="json"),
        "schema_version": 2,
        "candidate_revision": REVISION,
        "api_package_sha256": "c" * 64,
        "api_package_files": 300,
        "worker_package_sha256": "c" * 64,
        "worker_package_files": 300,
        "evaluation_policy_sha256": "d" * 64,
        "configuration_sha256": dict.fromkeys(_CONFIG_PATHS_V2, canonical_sha256([])),
    }

    profile = AcceptanceProfile.model_validate(document)

    assert profile.schema_version == 2
    assert "/api/v1/admin/connectors/configuration-snapshot" in profile.configuration_sha256
    assert profile.candidate_revision == REVISION
    for missing in (
        "candidate_revision",
        "api_package_sha256",
        "api_package_files",
        "worker_package_sha256",
        "worker_package_files",
        "evaluation_policy_sha256",
    ):
        invalid = dict(document)
        invalid.pop(missing)
        with pytest.raises(ValidationError):
            AcceptanceProfile.model_validate(invalid)

    missing_connector = dict(document)
    missing_connector["configuration_sha256"] = {
        key: value
        for key, value in document["configuration_sha256"].items()
        if key != "/api/v1/admin/connectors/configuration-snapshot"
    }
    with pytest.raises(ValidationError):
        AcceptanceProfile.model_validate(missing_connector)


def test_h01_profile_candidate_cannot_be_reused_for_another_revision(frozen) -> None:
    _, legacy = frozen
    profile = AcceptanceProfile.model_validate(
        {
            **legacy.model_dump(mode="json"),
            "schema_version": 2,
            "candidate_revision": REVISION,
            "api_package_sha256": "c" * 64,
            "api_package_files": 300,
            "worker_package_sha256": "c" * 64,
            "worker_package_files": 300,
            "evaluation_policy_sha256": "d" * 64,
            "configuration_sha256": dict.fromkeys(_CONFIG_PATHS_V2, canonical_sha256([])),
        }
    )
    client = httpx.AsyncClient(base_url=profile.api_base_url)
    try:
        with pytest.raises(AcceptanceError, match="candidate_differs_from_frozen_profile"):
            AcceptanceRunner(client, profile, candidate="e" * 40)
    finally:
        asyncio.run(client.aclose())


async def test_internal_verified_never_substitutes_for_independent_scoring(frozen):
    report = await execute(frozen, SyntheticAPI())
    assert report["status"] == "BLOCKED"
    assert report["counts"]["NOT_RUN"] == 3
    assert report["answerable_accuracy"] == 0
    assert all(item["answer_sha256"] for item in report["results"])


async def test_candidate_preflight_blocks_all_tasks_and_keeps_cases(frozen):
    api = SyntheticAPI()
    api.identity["revision"] = "d" * 40
    report = await execute(frozen, api)
    assert api.turns == 0
    assert report["blocker"] == "deployed_candidate_or_image_mismatch"
    assert report["counts"]["NOT_RUN"] == 3


@pytest.mark.parametrize("failure", ["source_denied", "wrong_model", "postflight_revoke", "drift"])
async def test_permission_and_version_changes_block_scoring(frozen, failure):
    api, scorer = SyntheticAPI(), SyntheticScorer()
    setattr(api, failure, True)
    report = await execute(frozen, api, scorer)
    assert report["status"] == "BLOCKED"
    assert report["counts"]["BLOCKED"] == 3
    assert not scorer.observed
    assert len(report["results"]) == 3
    if failure == "drift":
        assert api.turns == 1


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "WAITING_USER", "WAITING_APPROVAL"])
async def test_failed_or_intervention_runs_are_failures(frozen, state):
    api = SyntheticAPI()
    api.run_state = state
    report = await execute(frozen, api)
    assert report["counts"]["FAIL"] == 3
    assert len(api.cancelled) == (3 if state.startswith("WAITING") else 0)


async def test_timeout_cancels_run_and_keeps_record(frozen):
    api = SyntheticAPI()
    api.run_state = "RUNNING"
    report = await execute(frozen, api)
    assert report["counts"]["FAIL"] == 3
    assert len(api.cancelled) == 3
    assert all(item["reason"] == "task_timeout" for item in report["results"])


async def test_ambiguous_artifacts_cannot_be_graded(frozen):
    api, scorer = SyntheticAPI(), SyntheticScorer()
    api.ambiguous = True
    report = await execute(frozen, api, scorer)
    assert report["counts"]["FAIL"] == 3
    assert not scorer.observed


@pytest.mark.parametrize("option", ["wrong_digest", "crash"])
async def test_invalid_or_failed_scorer_stays_blocked(frozen, option):
    report = await execute(frozen, SyntheticAPI(), SyntheticScorer(**{option: True}))
    assert report["counts"]["BLOCKED"] == 3
    assert "sensitive upstream" not in json.dumps(report)


async def test_existing_evidence_cannot_be_overwritten(frozen):
    await execute(frozen, SyntheticAPI())
    with pytest.raises(AcceptanceError, match="already_exists"):
        await execute(frozen, SyntheticAPI())


async def test_redirect_is_not_followed_with_acceptance_credentials(frozen):
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://untrusted.example/steal"})

    report = await execute(frozen, redirect)
    assert len(requests) == 1
    assert report["status"] == "BLOCKED"


def test_dataset_is_immutable_and_duplicate_or_optional_cases_are_rejected(frozen):
    root, profile = frozen
    path = root / profile.dataset
    document = json.loads(path.read_bytes())
    document["cases"][0]["required"] = False
    path.write_text(json.dumps(document))
    with pytest.raises(AcceptanceError, match="digest_mismatch"):
        load_frozen_cases(profile, root)
    updated = profile.model_copy(
        update={"dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    )
    with pytest.raises(ValidationError):
        load_frozen_cases(updated, root)
    document["cases"][0]["required"] = True
    document["cases"].append(document["cases"][0])
    path.write_text(json.dumps(document))
    updated = profile.model_copy(
        update={"dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    )
    with pytest.raises(AcceptanceError, match="duplicate_cases"):
        load_frozen_cases(updated, root)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/?token=abc",
        "https://example.com/api",
    ],
)
def test_profile_rejects_insecure_or_credential_bearing_origins(frozen, url):
    payload = frozen[1].model_dump(mode="json")
    payload["api_base_url"] = url
    with pytest.raises(ValidationError):
        AcceptanceProfile.model_validate(payload)


def test_cli_requires_exact_phase_profile_candidate_and_output():
    args = build_parser().parse_args(
        [
            "acceptance",
            "run",
            "--phase",
            "P1",
            "--profile",
            "staging",
            "--candidate",
            REVISION,
            "--output",
            "acceptance-example",
        ]
    )
    assert args.phase == "P1" and args.candidate == REVISION


def test_runtime_identity_reports_missing_pins_honestly(client):
    response = client.get("/api/v1/admin/runtime-identity")
    assert response.status_code == 200
    identity = response.json()
    assert identity["revision"] is None
    assert identity["image_digest"] is None
    assert identity["environment"] == "test"
    assert identity["provenance"] == "deployment_configuration"
    assert identity["observation"] == "installed_package_at_api_initialization"
    assert identity["signature_verified"] is False
    assert len(identity["package_sha256"]) == 64
    assert identity["package_files"] > 250


def test_driver_uses_real_application_routes_and_published_answer(
    client, app_settings, monkeypatch, frozen
):
    import asyncio
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy import select

    from obsion.db.models import Evidence
    from obsion.model_gateway.gateway import ModelGateway, ModelResult

    fact = "财务报销申请人需要提供合法合规的发票。"

    async def synthetic_model(self, session, **kwargs):
        sources = list(
            await session.scalars(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        )
        source = next(item for item in sources if fact in str(item.content))
        payload = (
            {
                "answerable": True,
                "answer": fact,
                "claims": [{"statement": fact, "evidence_ids": [str(source.id)]}],
            }
            if kwargs["step_id"] is None
            else {
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
        )
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

    monkeypatch.setattr(ModelGateway, "complete", synthetic_model)
    monkeypatch.setattr(app_settings, "release_revision", REVISION)
    monkeypatch.setattr(app_settings, "release_image_digest", IMAGE)
    document = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("finance.md", ("# 财务报销制度\n" + fact).encode(), "text/markdown")},
        data={
            "source": "acceptance-driver-development",
            "external_id": "finance",
            "title": "财务报销制度",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    document_id = document.json()["document"]["id"]
    source = client.get(f"/api/v1/knowledge/documents/{document_id}/content")
    assert source.status_code == 200
    source_digest = hashlib.sha256(source.content).hexdigest()
    workspace = client.post("/api/v1/workspaces", json={"name": "Acceptance driver test"})
    assert workspace.status_code == 201
    model = client.get("/api/v1/admin/models/profiles").json()[0]
    agent = next(
        item
        for item in client.get("/api/v1/admin/agents").json()
        if item["name"] == "knowledge-agent"
    )
    root, profile = frozen
    raw = json.loads((root / profile.dataset).read_bytes())
    raw["sources"] = [{"document_id": document_id, "sha256": source_digest}]
    raw["cases"] = [
        dict(
            raw["cases"][0],
            source_document_id=document_id,
            source_sha256=source_digest,
            question="根据财务报销制度，申请人需要提供什么材料？",
            reviewed_source_quotes=[fact],
        )
    ]
    encoded = json.dumps(raw, ensure_ascii=False).encode()
    (root / profile.dataset).write_bytes(encoded)
    profile = AcceptanceProfile.model_validate(
        dict(
            profile.model_dump(mode="json"),
            dataset_sha256=hashlib.sha256(encoded).hexdigest(),
            workspace_id=workspace.json()["id"],
            model_profile=model["name"],
            model_profile_id=model["id"],
            agent_version_id=agent["version_id"],
            configuration_sha256={
                path: canonical_sha256(client.get(path).json()) for path in _CONFIG_PATHS
            },
            timeout_seconds=15,
        )
    )

    def application_transport(request):
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
        return httpx.Response(response.status_code, content=response.content)

    async def checked_execute():
        async with httpx.AsyncClient(
            base_url=profile.api_base_url, transport=httpx.MockTransport(application_transport)
        ) as transport:
            observed_profile = await AcceptanceRunner.freeze(
                transport,
                root,
                name=profile.name,
                candidate=REVISION,
                image_digest=IMAGE,
                workspace_id=profile.workspace_id,
                model_profile=profile.model_profile,
                dataset=profile.dataset,
                dataset_sha256=profile.dataset_sha256,
            )
            assert observed_profile.configuration_sha256 == profile.configuration_sha256
            assert observed_profile.agent_version_id == profile.agent_version_id
            assert observed_profile.model_profile_id == profile.model_profile_id
            runner = AcceptanceRunner(transport, observed_profile, candidate=REVISION)
            await runner._identity()
            first = await runner.run(root, root / "result")
            second = await runner.run(root, root / "reconciled", resume_from=root / "result")
            assert second["counts"] == first["counts"], second
            assert second["results"][0]["run_id"] == first["results"][0]["run_id"]
            assert second["results"][0].get("answer") == first["results"][0].get("answer")
            return second

    report = asyncio.run(checked_execute())
    assert report["counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 1}, report
    result = report["results"][0]
    assert "run_status" in result, report
    assert result["run_status"] == "COMPLETED"
    assert fact in result["answer"]
    assert result["steps"] and result["evidence_refs"]
    assert result["reason"] == "independent_semantic_scorer_not_configured"
