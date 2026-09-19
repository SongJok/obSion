"""Execution observations detect drift; synthetic workers are not live attestation."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_productization_acceptance import (
    IMAGE,
    REVISION,
    SyntheticAPI,
    SyntheticScorer,
    execute,
)
from test_productization_acceptance import (
    frozen as frozen,
)

from obsion.harness.execution_identity import ExecutionIdentity, snapshot_package

PACKAGE = "d" * 64


def test_package_snapshot_is_relocatable_and_includes_code_and_contract_changes(tmp_path):
    trees = [tmp_path / name for name in ("checkout", "wheel")]
    for tree in trees:
        tree.mkdir()
        (tree / "__init__.py").write_text("# package\n")
        (tree / "contract.json").write_text('{"version":1}')
        (tree / ".env").write_text("not part of an executable package observation")
        (tree / "__pycache__").mkdir()
        (tree / "__pycache__" / "cached.py").write_text("ignored")
    before = snapshot_package(trees[0])
    assert before == snapshot_package(trees[1])
    assert before.files == 2
    (trees[1] / "contract.json").write_text('{"version":2}')
    assert before.sha256 != snapshot_package(trees[1]).sha256
    (trees[1] / "contract.json").write_text('{"version":1}')
    (trees[1] / "__init__.py").write_text("# changed\n")
    assert before.sha256 != snapshot_package(trees[1]).sha256


@pytest.mark.parametrize("mode", ["missing", "symlink", "oversized"])
def test_package_observation_does_not_substitute_empty_partial_or_external_source(tmp_path, mode):
    if mode != "missing":
        (tmp_path / "__init__.py").write_text("# package")
    if mode == "symlink":
        (tmp_path / "external.py").symlink_to(tmp_path / "__init__.py")
    if mode == "oversized":
        with (tmp_path / "large.py").open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="package_snapshot"):
        snapshot_package(tmp_path)


def test_runtime_captures_pins_before_operator_configuration_changes(app_settings):
    app_settings.release_revision = REVISION
    app_settings.release_image_digest = IMAGE
    observed = ExecutionIdentity.observe(app_settings)
    app_settings.release_revision = "e" * 40
    first, second = observed.event_payload(), observed.event_payload()
    assert first["revision"] == REVISION
    assert first["runtime_instance_id"] == second["runtime_instance_id"]
    assert first["execution_id"] != second["execution_id"]
    assert first["signature_verified"] is False
    assert first["package_files"] > 250
    assert "path" not in json.dumps(first)


def test_resume_appends_own_observation_and_terminal_run_does_not_rewrite_history(
    client, monkeypatch
):
    from obsion.harness.runtime import HarnessRuntime

    worker = client.app.state.run_worker
    client.portal.call(worker.stop)
    workspace = client.post("/api/v1/workspaces", json={"name": "Observation test"}).json()
    thread = client.post(
        "/api/v1/threads", json={"workspace_id": workspace["id"], "title": "Observation task"}
    ).json()
    response = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "请分析 payment 错误率"}
    )
    assert response.status_code == 202
    run_id = response.json()["run"]["id"]
    org_id, claimed_id = client.portal.call(worker._claim)
    assert str(claimed_id) == run_id

    async def wait_in_steps(self, organization_id, run_id):
        return False

    monkeypatch.setattr(HarnessRuntime, "_execute_steps", wait_in_steps)
    client.portal.call(worker.runtime.execute, org_id, claimed_id)
    first = client.get(f"/api/v1/runs/{run_id}/events").json()
    observed = [e for e in first if e["name"] == "run.execution_observed"]
    assert len(observed) == 1
    assert observed[0]["run_sequence"] < next(
        e["run_sequence"] for e in first if e["name"] == "plan.created"
    )
    worker.runtime.execution_identity = replace(
        worker.runtime.execution_identity, runtime_instance_id=str(uuid4()), revision=REVISION
    )
    client.portal.call(worker.runtime.execute, org_id, claimed_id)
    events = client.get(f"/api/v1/runs/{run_id}/events").json()
    later = [e for e in events if e["name"] == "run.execution_observed"]
    assert len(later) == 2 and later[0] == observed[0]
    assert later[0]["payload"]["runtime_instance_id"] != later[1]["payload"]["runtime_instance_id"]
    assert later[0]["payload"]["revision"] is None
    assert later[1]["payload"]["revision"] == REVISION
    assert client.post(f"/api/v1/runs/{run_id}/cancel").is_success
    client.portal.call(worker.runtime._record_execution_identity, org_id, claimed_id)
    final = client.get(f"/api/v1/runs/{run_id}/events").json()
    assert [e for e in final if e["name"] == "run.execution_observed"] == later
    denied = client.get(
        f"/api/v1/runs/{run_id}/events", headers={"Authorization": "Bearer invalid"}
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "invalid_token"


class ExecutionAPI(SyntheticAPI):
    def __init__(self, mode="match"):
        super().__init__()
        self.mode = mode

    def __call__(self, request):
        if not request.url.path.endswith("/events"):
            return super().__call__(request)
        import httpx

        run_id = request.url.path.split("/")[-2]
        payload = {
            "execution_id": str(UUID(int=20)),
            "runtime_instance_id": str(UUID(int=30)),
            "revision": REVISION,
            "image_digest": IMAGE,
            "package_sha256": PACKAGE,
            "package_files": 300,
            "observation": "installed_package_at_runtime_initialization",
            "signature_verified": False,
        }
        event = {
            "id": str(UUID(int=10)),
            "aggregate_id": run_id,
            "aggregate_type": "run",
            "run_id": run_id,
            "run_sequence": 1,
            "name": "run.execution_observed",
            "schema_version": 1,
            "actor_type": "SYSTEM",
            "actor_id": None,
            "payload": payload,
        }
        events = [event]
        if self.mode in {"revision", "image_digest", "package_sha256"}:
            payload[self.mode] = "e" * 64
        if self.mode == "signed":
            payload["signature_verified"] = True
        if self.mode == "missing":
            events = []
        if self.mode == "wrong_run":
            event["run_id"] = str(uuid4())
        if self.mode == "resume":
            events.append(
                {**event, "run_sequence": 2, "payload": {**payload, "revision": "f" * 40}}
            )
        if self.mode == "duplicate":
            events.append({**event, "run_sequence": 2})
        if self.mode == "repeating_page":
            events = [{**event, "run_sequence": n, "name": "run.started"} for n in range(1, 201)]
        else:
            events.append({"run_id": run_id, "run_sequence": 3, "name": "run.completed"})
        return httpx.Response(200, json=events)


@pytest.mark.parametrize(
    "mode",
    [
        "revision",
        "image_digest",
        "package_sha256",
        "signed",
        "missing",
        "wrong_run",
        "resume",
        "duplicate",
        "repeating_page",
    ],
)
async def test_actual_worker_identity_is_checked_even_when_api_configuration_matches(frozen, mode):
    root, profile = frozen
    profile = profile.model_copy(update={"worker_package_sha256": PACKAGE})
    api, scorer = ExecutionAPI(mode), SyntheticScorer()
    report = await execute((root, profile), api, scorer)
    assert report["counts"] == {"PASS": 0, "FAIL": 0, "BLOCKED": 3, "NOT_RUN": 0}
    assert not scorer.observed
    assert all(r["reason"].startswith("worker_execution_") for r in report["results"])


async def test_matching_observation_preserves_independent_score_and_unsigned_gate(frozen):
    root, profile = frozen
    profile = profile.model_copy(update={"worker_package_sha256": PACKAGE})
    scorer = SyntheticScorer()
    report = await execute((root, profile), ExecutionAPI(), scorer)
    assert report["counts"] == {"PASS": 2, "FAIL": 1, "BLOCKED": 0, "NOT_RUN": 0}
    assert len(scorer.observed) == 3
    assert all(len(r["worker_executions"]) == 1 for r in report["results"])
    assert "signed_deployment_attestation_required" in report["phase_blockers"]
    assert report["phase_status"] == "BLOCKED" and not report["promotion_eligible"]


def test_legacy_profile_serialization_stays_byte_compatible(frozen):
    _, profile = frozen
    assert "worker_package_sha256" not in profile.model_dump(mode="json")
    assert profile.worker_package_sha256 is None


async def test_freeze_records_local_package_without_calling_it_signed_proof(frozen):
    import hashlib

    import httpx
    from test_productization_acceptance import AGENT_ID, PROFILE_ID

    from obsion.evaluations.acceptance import AcceptanceRunner

    root, old = frozen
    package = root / "services/control-plane/src/obsion"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# reviewed candidate\n")
    observed = snapshot_package(package)
    api = SyntheticAPI()
    api.identity.update(
        {
            "package_sha256": observed.sha256,
            "package_files": observed.files,
            "observation": "installed_package_at_api_initialization",
            "signature_verified": False,
        }
    )

    def freeze_api(request):
        if request.url.path.endswith("/connectors/configuration-snapshot"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/models/profiles"):
            return httpx.Response(
                200, json=[{"id": PROFILE_ID, "name": "test-only", "enabled": True}]
            )
        if request.url.path.endswith("/admin/agents"):
            return httpx.Response(
                200, json=[{"version_id": AGENT_ID, "name": "knowledge-agent", "status": "ACTIVE"}]
            )
        return api(request)

    async with httpx.AsyncClient(
        base_url=old.api_base_url, transport=httpx.MockTransport(freeze_api)
    ) as client:
        new = await AcceptanceRunner.freeze(
            client,
            root,
            name="observed-source-test",
            candidate=REVISION,
            image_digest=IMAGE,
            workspace_id=old.workspace_id,
            model_profile=old.model_profile,
            dataset=old.dataset,
            dataset_sha256=hashlib.sha256((root / old.dataset).read_bytes()).hexdigest(),
        )
    assert new.schema_version == 2
    assert new.candidate_revision == REVISION
    assert new.api_package_sha256 == observed.sha256
    assert new.api_package_files == observed.files
    assert new.worker_package_sha256 == observed.sha256
    assert new.worker_package_files == observed.files
    assert new.evaluation_policy_sha256 is not None
    assert "/api/v1/admin/connectors/configuration-snapshot" in new.configuration_sha256
    assert api.turns == 0
