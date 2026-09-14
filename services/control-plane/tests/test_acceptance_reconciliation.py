"""Restart reconciliation uses live task observations, never saved quality scores."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_productization_acceptance import (
    REVISION,
    SyntheticAPI,
    SyntheticScorer,
    execute,
)
from test_productization_acceptance import (
    frozen as frozen_fixture,
)

from obsion.evaluations.acceptance import AcceptanceError, AcceptanceRunner, write_private_json


@pytest.fixture(name="frozen")
def reconciliation_inputs(tmp_path):
    return frozen_fixture.__wrapped__(tmp_path)


async def reconcile(frozen, api, scorer=None, *, output="reconciled", previous="result"):
    root, profile = frozen
    async with httpx.AsyncClient(
        base_url=profile.api_base_url, transport=httpx.MockTransport(api)
    ) as client:
        return await AcceptanceRunner(client, profile, candidate=REVISION, scorer=scorer).run(
            root, root / output, resume_from=root / previous
        )


def edit_report(frozen, edit):
    path = frozen[0] / "result" / "report.json"
    report = json.loads(path.read_bytes())
    edit(report)
    write_private_json(path, report)


async def test_reconciliation_reobserves_and_rescores_without_creating_tasks(frozen):
    api = SyntheticAPI()
    first = await execute(frozen, api, SyntheticScorer())
    old_bytes = (frozen[0] / "result" / "report.json").read_bytes()
    api.requests.clear()
    second = await reconcile(frozen, api)
    assert api.turns == 3
    assert all(method == "GET" for method, _, _ in api.requests)
    assert first["counts"]["PASS"] == 2
    assert second["counts"]["NOT_RUN"] == 3
    assert [r["run_id"] for r in first["results"]] == [r["run_id"] for r in second["results"]]
    assert second["reconciles_report_sha256"] == hashlib.sha256(old_bytes).hexdigest()
    assert (frozen[0] / "result" / "report.json").read_bytes() == old_bytes
    assert not second["promotion_eligible"] and second["phase_status"] == "BLOCKED"


@pytest.mark.parametrize("previous_timeout", [False, True])
async def test_failed_preflight_keeps_locations_and_failure_across_repeated_resumes(
    frozen, previous_timeout
):
    api = SyntheticAPI()
    original = await execute(frozen, api)
    if previous_timeout:
        edit_report(
            frozen,
            lambda r: [item.update(status="FAIL", reason="task_timeout") for item in r["results"]],
        )
    api.drift = True
    blocked = await reconcile(frozen, api)
    assert blocked["blocker"] == "runtime_configuration_drift"
    assert [r["run_id"] for r in blocked["results"]] == [r["run_id"] for r in original["results"]]
    assert all("answer" not in r and "score" not in r for r in blocked["results"])
    api.drift = False
    api.requests.clear()
    scorer = SyntheticScorer()
    recovered = await reconcile(frozen, api, scorer, output="third", previous="reconciled")
    assert recovered["counts"]["FAIL"] == (3 if previous_timeout else 1)
    assert len(scorer.observed) == (0 if previous_timeout else 3)
    assert api.turns == 3 and all(method == "GET" for method, _, _ in api.requests)


async def test_unknown_admissions_and_never_started_cases_are_not_resubmitted(frozen):
    api = SyntheticAPI()
    await execute(frozen, api)

    def incomplete(report):
        for index, result in enumerate(report["results"]):
            result.pop("run_id")
            result.pop("thread_id")
            if index == 2:
                result.pop("admission_state")

    edit_report(frozen, incomplete)
    api.requests.clear()
    report = await reconcile(frozen, api)
    assert report["counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 2, "NOT_RUN": 1}
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_process_interruption_before_run_response_preserves_uncertainty(frozen):
    api = SyntheticAPI()

    def interrupted(request):
        response = api(request)
        if request.method == "POST" and request.url.path.endswith("/turns"):
            raise asyncio.CancelledError()
        return response

    with pytest.raises(asyncio.CancelledError):
        await execute(frozen, interrupted)
    persisted = json.loads((frozen[0] / "result" / "report.json").read_bytes())
    assert persisted["results"][0]["admission_state"] == "RUN_REQUESTED"
    assert "run_id" not in persisted["results"][0]
    api.requests.clear()
    report = await reconcile(frozen, api)
    assert api.turns == 1
    assert report["counts"]["BLOCKED"] == 1 and report["counts"]["NOT_RUN"] == 2
    assert all(method == "GET" for method, _, _ in api.requests)


async def test_crash_after_known_run_can_resume_with_fresh_answer_and_score(frozen):
    api = SyntheticAPI()

    class InterruptedScorer:
        async def score(self, **kwargs):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await execute(frozen, api, InterruptedScorer())
    persisted = json.loads((frozen[0] / "result" / "report.json").read_bytes())
    assert persisted["results"][0]["run_id"]

    def changed_answer(request):
        response = api(request)
        if request.url.path.endswith("/artifacts"):
            value = response.json()
            value[0]["inline_content"]["markdown"] = "重新读取的实际发布答案。"
            return httpx.Response(200, json=value)
        return response

    api.requests.clear()
    scorer = SyntheticScorer()
    report = await reconcile(frozen, changed_answer, scorer)
    assert report["counts"] == {"PASS": 1, "FAIL": 0, "BLOCKED": 0, "NOT_RUN": 2}
    assert len(scorer.observed) == 1
    assert scorer.observed[0]["answer"] == "重新读取的实际发布答案。"
    assert api.turns == 1
    assert all(method == "GET" for method, _, _ in api.requests)


@pytest.mark.parametrize("failure", ["source_denied", "postflight_revoke", "drift", "wrong_model"])
async def test_resumed_tasks_recheck_sources_configuration_and_model(frozen, failure):
    api = SyntheticAPI()
    await execute(frozen, api, SyntheticScorer())
    setattr(api, failure, True)
    scorer = SyntheticScorer()
    report = await reconcile(frozen, api, scorer)
    assert report["status"] == "BLOCKED"
    assert report["counts"]["PASS"] == 0
    assert not scorer.observed
    assert all("answer" not in r for r in report["results"])


@pytest.mark.parametrize(
    "change", ["candidate", "case_inventory", "duplicate_run", "profile", "inputs"]
)
async def test_frozen_reconciliation_inputs_cannot_be_replaced(frozen, change):
    api = SyntheticAPI()
    await execute(frozen, api)
    if change == "candidate":
        edit_report(frozen, lambda r: r.update(candidate="f" * 40))
    elif change == "case_inventory":
        edit_report(frozen, lambda r: r["results"].pop())
    elif change == "duplicate_run":
        edit_report(frozen, lambda r: r["results"][1].update(run_id=r["results"][0]["run_id"]))
    else:
        write_private_json(frozen[0] / "result" / f"{change}.json", {})
    api.requests.clear()
    with pytest.raises(AcceptanceError):
        await reconcile(frozen, api)
    assert not api.requests
    assert not (frozen[0] / "reconciled").exists()


@pytest.mark.parametrize("mismatch", ["workspace", "run", "question", "context", "timestamp"])
async def test_saved_ids_are_verified_against_current_task_relations(frozen, mismatch):
    api = SyntheticAPI()
    await execute(frozen, api)

    def mismatched(request):
        response = api(request)
        path = request.url.path
        if request.method == "GET":
            value = response.json()
            if path.endswith("/threads") and mismatch == "workspace":
                return httpx.Response(200, json=[])
            if path.endswith("/runs"):
                if mismatch == "run":
                    return httpx.Response(200, json=[])
                if mismatch == "timestamp":
                    value[0]["completed_at"] = None
                    return httpx.Response(200, json=value)
            if path.endswith("/turns") and mismatch in {"question", "context"}:
                value[0]["input_text" if mismatch == "question" else "context_refs"] = (
                    "不同问题" if mismatch == "question" else [{"type": "task_context"}]
                )
                return httpx.Response(200, json=value)
        return response

    scorer = SyntheticScorer()
    report = await reconcile(frozen, mismatched, scorer)
    assert report["counts"]["BLOCKED"] == 3
    assert not scorer.observed


async def test_restart_does_not_reset_original_task_deadline(frozen):
    api = SyntheticAPI()
    await execute(frozen, api)
    api.run_state = "RUNNING"
    start = datetime.now(UTC) - timedelta(days=1)

    def expired(report):
        for result in report["results"]:
            result.update(
                requested_at=start.isoformat(),
                deadline_at=(start + timedelta(seconds=1)).isoformat(),
            )

    edit_report(frozen, expired)
    report = await reconcile(frozen, api, SyntheticScorer())
    assert report["counts"]["FAIL"] == 3
    assert all(r["reason"] == "task_timeout" for r in report["results"])
    assert len(api.cancelled) == 3


async def test_prior_timeout_cannot_become_a_pass_after_late_completion(frozen):
    api = SyntheticAPI()
    await execute(frozen, api)
    edit_report(
        frozen,
        lambda r: [item.update(status="FAIL", reason="task_timeout") for item in r["results"]],
    )
    scorer = SyntheticScorer()
    report = await reconcile(frozen, api, scorer)
    assert report["counts"]["FAIL"] == 3
    assert not scorer.observed


async def test_failed_cancellation_preserves_failure_and_reports_unknown_cleanup(frozen):
    api = SyntheticAPI()
    api.run_state = "WAITING_USER"

    def unavailable_cancel(request):
        if request.url.path.endswith("/cancel"):
            raise httpx.ReadError("sensitive provider error")
        return api(request)

    report = await execute(frozen, unavailable_cancel)
    assert report["counts"]["FAIL"] == 3
    assert all(r["cancel_status"] == "UNKNOWN" for r in report["results"])
    assert "sensitive provider" not in json.dumps(report)
