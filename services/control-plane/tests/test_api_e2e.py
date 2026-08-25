import time
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def create_workspace(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": "Reliability", "description": "Production investigation workspace"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_capability_catalog_and_data_query_contracts_are_exposed(client: TestClient) -> None:
    capabilities = client.get("/api/v1/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    assert capabilities.json()
    capability = client.get(f"/api/v1/capabilities/{capabilities.json()[0]['id']}")
    assert capability.status_code == 200, capability.text
    assert capability.json()["version_id"]

    workspace = create_workspace(client)
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Governed data query"},
    ).json()
    unresolved = client.post(
        "/api/v1/data/query",
        json={"thread_id": thread["id"], "question": "an unregistered business measure"},
    )
    assert unresolved.status_code == 422, unresolved.text
    assert unresolved.json()["code"] == "metric_not_resolved"

    for path in (
        "/api/v1/admin/departments",
        "/api/v1/admin/data/sources",
        "/api/v1/admin/data/catalog",
        "/api/v1/admin/costs",
        "/api/v1/admin/prompts",
        "/api/v1/admin/knowledge",
        "/api/v1/admin/secrets",
    ):
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
    secrets = client.get("/api/v1/admin/secrets").json()
    assert all("external_ref" not in item and "encrypted_envelope" not in item for item in secrets)
    secret = client.post(
        "/api/v1/admin/secrets",
        json={
            "name": "warehouse-reader",
            "provider": "env",
            "external_ref": "env://OBSION_WAREHOUSE_DSN",
            "description": "Read-only warehouse identity",
        },
    )
    assert secret.status_code == 201, secret.text
    secret_metadata = client.get("/api/v1/admin/secrets").json()[0]
    assert secret_metadata["name"] == "warehouse-reader"
    assert "external_ref" not in secret_metadata

    prompt = client.post(
        "/api/v1/admin/prompts",
        json={
            "name": "verified-answer",
            "display_name": "Verified answer",
            "description": "Answer contract",
            "template": "Answer from {{ evidence }} only.",
            "variables_schema": {
                "type": "object",
                "properties": {"evidence": {"type": "array"}},
            },
        },
    )
    assert prompt.status_code == 201, prompt.text
    assert prompt.json()["status"] == "DRAFT"
    assert client.get("/api/v1/admin/prompts").json()[0]["checksum_sha256"]
    invalid_prompt = client.post(
        "/api/v1/admin/prompts",
        json={
            "name": "invalid-schema",
            "display_name": "Invalid schema",
            "template": "Answer from evidence.",
            "variables_schema": {"type": "not-a-json-schema-type"},
        },
    )
    assert invalid_prompt.status_code == 422
    assert invalid_prompt.json()["code"] == "prompt_variables_schema_invalid"


def test_governed_knowledge_run_is_replayable(client: TestClient) -> None:
    workspace = create_workspace(client)
    document = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "policy.md",
                b"# Release policy\nEvery production release requires an owner and rollback plan.",
                "text/markdown",
            )
        },
        data={
            "source": "test-suite",
            "external_id": "release-policy-v1",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    assert document.json()["chunk_count"] == 1
    downloaded_document = client.get(
        f"/api/v1/knowledge/documents/{document.json()['document']['id']}/content"
    )
    assert downloaded_document.status_code == 200
    assert b"production release requires" in downloaded_document.content
    denied = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "private.md",
                b"rollback plan rollback plan private payroll",
                "text/markdown",
            )
        },
        data={
            "source": "test-suite",
            "external_id": "private-policy-v1",
            "title": "Private policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true, "deny_users": ["00000000-0000-7000-8000-000000000002"]}',
        },
    )
    assert denied.status_code == 201, denied.text
    search = client.post("/api/v1/knowledge/search", json={"query": "rollback plan", "limit": 20})
    assert search.status_code == 200, search.text
    assert {item["title"] for item in search.json()} == {"Release policy"}

    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Release controls"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "What does the release policy require?"},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run"]["id"]

    run = created.json()["run"]
    for _ in range(80):
        run_response = client.get(f"/api/v1/runs/{run_id}")
        assert run_response.status_code == 200, run_response.text
        run = run_response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert run["status"] == "COMPLETED", run

    events = client.get(f"/api/v1/runs/{run_id}/events").json()
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert {"policy.decided", "evidence.created", "critic.completed", "run.completed"}.issubset(
        {event["name"] for event in events}
    )
    source_steps = client.get(f"/api/v1/runs/{run_id}/steps").json()
    capability_steps = [item for item in source_steps if item["kind"] == "CAPABILITY"]
    assert capability_steps
    assert all(item["capability_version_id"] for item in capability_steps)
    source_evidence = client.get(f"/api/v1/runs/{run_id}/evidence").json()
    assert source_evidence
    claims = client.get(f"/api/v1/runs/{run_id}/claims").json()
    assert claims[0]["verification_status"] == "VERIFIED"
    source_artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()
    artifact = source_artifacts[0]
    assert "rollback plan" in artifact["inline_content"]["markdown"]
    assert artifact["inline_content"]["verification"]["verified"] is True

    replay = client.post(f"/api/v1/runs/{run_id}/replay")
    assert replay.status_code == 202, replay.text
    assert replay.json()["replay_of_run_id"] == run_id
    replay_id = replay.json()["id"]
    replay_run = replay.json()
    for _ in range(80):
        replay_run = client.get(f"/api/v1/runs/{replay_id}").json()
        if replay_run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert replay_run["status"] == "COMPLETED", replay_run
    assert replay_run["intent"] == run["intent"]
    assert replay_run["plan"] == run["plan"]
    assert replay_run["step_count"] == run["step_count"]
    assert replay_run["input_tokens"] == run["input_tokens"]
    assert replay_run["output_tokens"] == run["output_tokens"]
    assert replay_run["cost_amount"] == run["cost_amount"]

    replay_events = client.get(f"/api/v1/runs/{replay_id}/events").json()
    assert [item["sequence"] for item in replay_events] == list(range(1, len(replay_events) + 1))
    replay_event_names = {item["name"] for item in replay_events}
    assert {"run.replay.started", "run.replay.event", "run.replay.completed"}.issubset(
        replay_event_names
    )
    assert "capability.requested" not in replay_event_names
    assert "tool.started" not in replay_event_names
    replay_completed = next(
        item for item in replay_events if item["name"] == "run.replay.completed"
    )
    assert replay_completed["payload"]["source_run_id"] == run_id
    assert replay_completed["payload"]["evidence"] == len(source_evidence)
    assert len(replay_completed["payload"]["snapshot_sha256"]) == 64

    replay_steps = client.get(f"/api/v1/runs/{replay_id}/steps").json()
    assert [item["capability_version_id"] for item in replay_steps] == [
        item["capability_version_id"] for item in source_steps
    ]
    replay_evidence = client.get(f"/api/v1/runs/{replay_id}/evidence").json()
    assert [item["content_fingerprint"] for item in replay_evidence] == [
        item["content_fingerprint"] for item in source_evidence
    ]
    assert [item["content"] for item in replay_evidence] == [
        item["content"] for item in source_evidence
    ]
    assert all(item["lineage"]["replay_source_run_id"] == run_id for item in replay_evidence)

    replay_claims = client.get(f"/api/v1/runs/{replay_id}/claims").json()
    assert [item["statement"] for item in replay_claims] == [item["statement"] for item in claims]
    replay_evidence_ids = {item["id"] for item in replay_evidence}
    assert all(set(item["evidence_ids"]).issubset(replay_evidence_ids) for item in replay_claims)
    assert not replay_evidence_ids.intersection(
        evidence_id for item in claims for evidence_id in item["evidence_ids"]
    )

    replay_artifacts = client.get(f"/api/v1/runs/{replay_id}/artifacts").json()
    assert [item["inline_content"] for item in replay_artifacts] != [
        item["inline_content"] for item in source_artifacts
    ]
    assert (
        replay_artifacts[0]["inline_content"]["markdown"] == artifact["inline_content"]["markdown"]
    )
    assert replay_artifacts[0]["lineage"]["replay_source_run_id"] == run_id

    second_replay = client.post(f"/api/v1/runs/{run_id}/replay")
    assert second_replay.status_code == 202, second_replay.text
    second_replay_id = second_replay.json()["id"]
    second_replay_run = second_replay.json()
    for _ in range(80):
        second_replay_run = client.get(f"/api/v1/runs/{second_replay_id}").json()
        if second_replay_run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert second_replay_run["status"] == "COMPLETED", second_replay_run
    second_replay_events = client.get(f"/api/v1/runs/{second_replay_id}/events").json()
    second_replay_completed = next(
        item for item in second_replay_events if item["name"] == "run.replay.completed"
    )
    assert (
        second_replay_completed["payload"]["snapshot_sha256"]
        == replay_completed["payload"]["snapshot_sha256"]
    )


def test_memory_requires_scope_and_redacts_candidates(client: TestClient) -> None:
    workspace = create_workspace(client)
    candidate = client.post(
        "/api/v1/memories",
        json={
            "scope": "WORKSPACE",
            "owner_ref": workspace["id"],
            "content": {"preference": "Use UTC", "api_key": "must-never-persist"},
            "sensitivity": "INTERNAL",
        },
    )
    assert candidate.status_code == 201, candidate.text
    body = candidate.json()
    assert body["status"] == "CANDIDATE"
    assert body["content"]["api_key"] == "[REDACTED]"

    duplicate = client.post(
        "/api/v1/memories",
        json={
            "scope": "WORKSPACE",
            "owner_ref": workspace["id"],
            "content": {"preference": "Use UTC", "api_key": "another-secret"},
            "sensitivity": "INTERNAL",
        },
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == body["id"]

    approved = client.post(
        f"/api/v1/memories/{body['id']}/approve",
        json={"reason": "Validated workspace preference"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    listed = client.get(
        "/api/v1/memories",
        params={"scope": "WORKSPACE", "owner_ref": workspace["id"], "status": "APPROVED"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]


def test_workspace_attachment_becomes_untrusted_evidence(client: TestClient) -> None:
    workspace = create_workspace(client)
    uploaded = client.post(
        f"/api/v1/workspaces/{workspace['id']}/artifacts",
        files={
            "file": (
                "investigation.txt",
                b"The controlled attachment says the recovery objective is 17 minutes.",
                "text/plain",
            )
        },
        data={"title": "Recovery objective", "kind": "FILE"},
    )
    assert uploaded.status_code == 201, uploaded.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Attachment analysis"},
    ).json()
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={
            "input": "Summarize the attached recovery objective",
            "attachment_refs": [{"type": "artifact", "artifact_id": uploaded.json()["id"]}],
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run"]["id"]
    run = created.json()["run"]
    for _ in range(80):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert run["status"] == "COMPLETED", run
    evidence = client.get(f"/api/v1/runs/{run_id}/evidence").json()
    attached = [item for item in evidence if item["source"] == "workspace-artifact"]
    assert attached[0]["content"]["text"].endswith("17 minutes.")
    answer = client.get(f"/api/v1/runs/{run_id}/artifacts").json()[0]
    assert "17 minutes" in answer["inline_content"]["markdown"]


def test_version_pinned_evaluation_records_case_results(client: TestClient) -> None:
    workspace = create_workspace(client)
    document = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "evaluation-policy.md",
                b"# Evaluation release gate\nEvery release gate requires immutable evidence.",
                "text/markdown",
            )
        },
        data={
            "source": "test-suite",
            "external_id": "evaluation-release-gate-v1",
            "title": "Evaluation release gate",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Evaluation source Run"},
    ).json()
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "What does the evaluation release gate require?"},
    )
    assert created.status_code == 202, created.text
    source_run = created.json()["run"]
    for _ in range(80):
        source_run = client.get(f"/api/v1/runs/{source_run['id']}").json()
        if source_run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert source_run["status"] == "COMPLETED", source_run
    assert source_run["agent_version_id"]
    assert source_run["model_profile_id"]

    dataset = client.post(
        "/api/v1/admin/evaluations/datasets",
        json={
            "name": "Routing and SQL safety",
            "description": "Release gate for deterministic control-plane behavior",
            "domain": "foundation",
        },
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["id"]
    route_case = client.post(
        f"/api/v1/admin/evaluations/datasets/{dataset_id}/cases",
        json={
            "external_id": "route-knowledge-001",
            "version": 1,
            "evaluator": "ROUTING",
            "input_payload": {"question": "Summarize the employee handbook"},
            "expected": {"route": "KNOWLEDGE"},
            "fixtures": {},
        },
    )
    assert route_case.status_code == 201, route_case.text
    sql_case = client.post(
        f"/api/v1/admin/evaluations/datasets/{dataset_id}/cases",
        json={
            "external_id": "deny-delete-001",
            "version": 1,
            "evaluator": "SQL_POLICY",
            "input_payload": {"sql": "delete from analytics.orders", "dialect": "postgres"},
            "expected": {"sql_allowed": False},
            "fixtures": {"allowed_tables": ["analytics.orders"]},
        },
    )
    assert sql_case.status_code == 201, sql_case.text
    run_case = client.post(
        f"/api/v1/admin/evaluations/datasets/{dataset_id}/cases",
        json={
            "external_id": "knowledge-output-001",
            "version": 1,
            "evaluator": "RUN_OUTPUT",
            "input_payload": {"run_ref": "knowledge-release-gate"},
            "expected": {
                "run_status": "COMPLETED",
                "route": "KNOWLEDGE",
                "capabilities": ["knowledge.search"],
                "evidence_types": ["DOCUMENT"],
                "answer_contains": ["immutable evidence"],
                "minimum_evidence_coverage": 1.0,
                "minimum_citation_precision": 1.0,
                "minimum_answer_faithfulness": 1.0,
            },
            "fixtures": {},
        },
    )
    assert run_case.status_code == 201, run_case.text

    fake_answer_case = client.post(
        f"/api/v1/admin/evaluations/datasets/{dataset_id}/cases",
        json={
            "external_id": "fake-answer-denied",
            "version": 1,
            "input_payload": {"question": "This is not a recorded Run"},
            "expected": {"contains": ["fabricated"]},
            "fixtures": {"actual": "fabricated"},
        },
    )
    assert fake_answer_case.status_code == 422
    assert fake_answer_case.json()["code"] == "evaluation_target_required"

    evaluation = client.post(
        f"/api/v1/admin/evaluations/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": source_run["agent_version_id"],
            "model_profile_id": source_run["model_profile_id"],
            "application_revision": "test-revision",
            "score_thresholds": {
                "evidence_coverage": 1.0,
                "citation_precision": 1.0,
                "answer_faithfulness": 1.0,
            },
            "run_bindings": {"knowledge-release-gate": source_run["id"]},
        },
    )
    assert evaluation.status_code == 201, evaluation.text
    body = evaluation.json()
    assert body["status"] == "COMPLETED"
    assert body["gate_passed"] is True
    assert body["metrics"]["pass_rate"] == 1.0
    assert body["metrics"]["passed"] == 3
    assert len(body["dataset_snapshot_sha256"]) == 64
    assert len(body["snapshot_sha256"]) == 64
    assert body["configuration_snapshot"]["agent"]["version_id"] == source_run[
        "agent_version_id"
    ]
    assert body["configuration_snapshot"]["capabilities"]
    bound_run = body["configuration_snapshot"]["bound_runs"][0]
    assert bound_run["run_id"] == source_run["id"]
    assert bound_run["capability_versions"][0]["name"] == "knowledge.search"

    results = client.get(f"/api/v1/admin/evaluations/runs/{body['id']}/results")
    assert results.status_code == 200, results.text
    case_results = results.json()
    assert [item["ordinal"] for item in case_results] == [1, 2, 3]
    assert all(item["status"] == "PASSED" for item in case_results)
    assert all(len(item["case_snapshot_sha256"]) == 64 for item in case_results)
    run_result = next(item for item in case_results if item["evaluator"] == "RUN_OUTPUT")
    assert run_result["evidence_refs"]
    assert run_result["observed"]["answer_sha256"]
    assert "immutable evidence" not in str(run_result["observed"])

    baseline = client.post(
        f"/api/v1/admin/evaluations/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": source_run["agent_version_id"],
            "model_profile_id": source_run["model_profile_id"],
            "application_revision": "test-revision-2",
            "baseline_run_id": body["id"],
            "score_thresholds": {
                "evidence_coverage": 1.0,
                "citation_precision": 1.0,
                "answer_faithfulness": 1.0,
            },
            "run_bindings": {"knowledge-release-gate": source_run["id"]},
        },
    )
    assert baseline.status_code == 201, baseline.text
    compared = baseline.json()
    assert compared["gate_passed"] is True
    assert compared["dataset_snapshot_sha256"] == body["dataset_snapshot_sha256"]
    assert compared["snapshot_sha256"] != body["snapshot_sha256"]
    assert compared["metrics"]["baseline"] == {
        "compared": True,
        "regressions": [],
        "improvements": [],
        "regression_rate": 0.0,
    }
    fetched = client.get(f"/api/v1/admin/evaluations/runs/{compared['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["baseline_run_id"] == body["id"]

    other_tenant = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="evaluation-intruder",
        display_name="Evaluation intruder",
        permissions=frozenset({"evaluations.read"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other_tenant
    try:
        assert client.get(f"/api/v1/admin/evaluations/runs/{body['id']}").status_code == 404
        assert (
            client.get(f"/api/v1/admin/evaluations/runs/{body['id']}/results").status_code
            == 404
        )
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_membership_is_enforced_for_runs_and_writes(client: TestClient) -> None:
    created_user = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "workspace-reader",
            "email": "workspace-reader@obsion.dev",
            "display_name": "Workspace Reader",
            "department": "Support",
            "attributes": {},
        },
    )
    assert created_user.status_code == 201, created_user.text
    user_id = created_user.json()["id"]
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Shared investigation", "visibility": "WORKSPACE"},
    ).json()
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Controlled thread"},
    ).json()
    turn = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "Explain the release policy"},
    )
    assert turn.status_code == 202
    run_id = turn.json()["run"]["id"]
    memory = client.post(
        "/api/v1/memories",
        json={
            "scope": "WORKSPACE",
            "owner_ref": workspace["id"],
            "content": {"timezone": "UTC"},
            "sensitivity": "INTERNAL",
        },
    )
    assert memory.status_code == 201, memory.text
    artifact = client.post(
        f"/api/v1/workspaces/{workspace['id']}/artifacts",
        files={"file": ("report.txt", b"governed artifact content", "text/plain")},
        data={"title": "Investigation report", "kind": "REPORT"},
    )
    assert artifact.status_code == 201, artifact.text
    artifact_id = artifact.json()["id"]

    reader = Principal(
        id=UUID(user_id),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="workspace-reader",
        display_name="Workspace Reader",
        permissions=frozenset({"memory.read"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: reader
    try:
        assert client.get("/api/v1/workspaces").json() == []
        assert client.get(f"/api/v1/runs/{run_id}").status_code == 404
        assert client.get(f"/api/v1/runs/{run_id}/events").status_code == 404
        assert client.get("/api/v1/memories").json() == []
        assert client.get(f"/api/v1/artifacts/{artifact_id}/content").status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    membership = client.put(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"user_id": user_id, "permissions": ["read"]},
    )
    assert membership.status_code == 200, membership.text

    client.app.dependency_overrides[get_principal] = lambda: reader
    try:
        assert [item["id"] for item in client.get("/api/v1/workspaces").json()] == [workspace["id"]]
        assert client.get(f"/api/v1/runs/{run_id}").status_code == 200
        assert [item["id"] for item in client.get("/api/v1/memories").json()] == [
            memory.json()["id"]
        ]
        downloaded = client.get(f"/api/v1/artifacts/{artifact_id}/content")
        assert downloaded.status_code == 200
        assert downloaded.content == b"governed artifact content"
        assert downloaded.headers["X-Content-Type-Options"] == "nosniff"
        denied_write = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"input": "This reader must not execute"},
        )
        assert denied_write.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    upgraded = client.put(
        f"/api/v1/workspaces/{workspace['id']}/members",
        json={"user_id": user_id, "permissions": ["read", "write"]},
    )
    assert upgraded.status_code == 200, upgraded.text
    client.app.dependency_overrides[get_principal] = lambda: reader
    try:
        allowed_write = client.post(
            f"/api/v1/threads/{thread['id']}/turns",
            json={"input": "This collaborator may execute"},
        )
        assert allowed_write.status_code == 202, allowed_write.text
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
