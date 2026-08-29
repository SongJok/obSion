import json

import httpx
import pytest

from obsion_sdk import AsyncObsionClient, ObsionAPIError


@pytest.mark.asyncio
async def test_client_sends_auth_and_decodes_workspace() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        assert request.url.path == "/api/v1/workspaces"
        return httpx.Response(200, json=[{"id": "workspace-1"}])

    async with AsyncObsionClient(
        "https://obsion.example",
        token="token",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await client.list_workspaces() == [{"id": "workspace-1"}]


@pytest.mark.asyncio
async def test_client_exposes_structured_api_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            403,
            json={"code": "denied", "message": "Denied", "correlation_id": "request-1"},
        )

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ObsionAPIError) as captured:
            await client.get_run("run-1")
    assert captured.value.status_code == 403
    assert captured.value.code == "denied"
    assert captured.value.correlation_id == "request-1"


@pytest.mark.asyncio
async def test_client_resumes_and_parses_sse_events() -> None:
    body = "id: 4\ndata: " + json.dumps({"sequence": 4}) + "\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Last-Event-ID"] == "3"
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        events = [event async for event in client.stream_events("run-1", after=3)]
    assert events == [{"sequence": 4}]


@pytest.mark.asyncio
async def test_client_exposes_complete_thread_lifecycle() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "thread-1", "status": "ACTIVE"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_threads("workspace-1", include_archived=True)
        await client.archive_thread("thread-1")
        await client.resume_thread("thread-1")
        await client.fork_thread("thread-1", "Alternative investigation", from_turn_id="turn-4")
        await client.list_thread_events("thread-1", after_sequence=3, limit=25)

    assert requests == [
        (
            "GET",
            "https://obsion.example/api/v1/workspaces/workspace-1/threads?include_archived=true",
            {},
        ),
        ("POST", "https://obsion.example/api/v1/threads/thread-1/archive", {}),
        ("POST", "https://obsion.example/api/v1/threads/thread-1/resume", {}),
        (
            "POST",
            "https://obsion.example/api/v1/threads/thread-1/fork",
            {"title": "Alternative investigation", "from_turn_id": "turn-4"},
        ),
        (
            "GET",
            "https://obsion.example/api/v1/threads/thread-1/events?after_sequence=3&limit=25",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_client_exposes_governed_data_and_knowledge_flows() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.url.path, body))
        if request.url.path == "/api/v1/capabilities":
            return httpx.Response(200, json=[{"name": "knowledge.search"}])
        if request.url.path == "/api/v1/data/query":
            return httpx.Response(202, json={"turn": {"id": "turn-1"}, "run": {"id": "run-1"}})
        if request.url.path == "/api/v1/admin/data/catalog":
            return httpx.Response(200, json={"metrics": 1})
        if request.url.path.startswith("/api/v1/data/sql/"):
            return httpx.Response(200, json={"id": "semantic-1"})
        if request.url.path.startswith("/api/v1/admin/data/"):
            return httpx.Response(201, json={"id": "semantic-1", "version": 1})
        return httpx.Response(200, json=[{"document_id": "doc-1"}])

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        assert (await client.list_capabilities())[0]["name"] == "knowledge.search"
        data = await client.query_data("thread-1", "上周收入是多少？")
        assert data["run"]["id"] == "run-1"
        assert (await client.search_knowledge("发布流程", limit=4))[0]["document_id"] == "doc-1"
        assert (await client.validate_sql("SELECT 1", "source-1"))["id"] == "semantic-1"
        assert (await client.explain_sql("SELECT 1 LIMIT 1", "source-1"))["id"] == "semantic-1"
        assert (await client.get_data_catalog())["metrics"] == 1
        definition = {"name": "paid_user_count"}
        assert (await client.create_metric(definition))["id"] == "semantic-1"
        assert (await client.create_dimension(definition))["id"] == "semantic-1"
        assert (await client.create_entity(definition))["id"] == "semantic-1"
        assert (await client.create_relation(definition))["id"] == "semantic-1"
        assert (await client.create_business_rule(definition))["id"] == "semantic-1"
        assert (await client.create_time_definition(definition))["id"] == "semantic-1"
        assert (await client.create_semantic_synonym(definition))["id"] == "semantic-1"

    assert requests == [
        ("/api/v1/capabilities", {}),
        ("/api/v1/data/query", {"thread_id": "thread-1", "question": "上周收入是多少？"}),
        ("/api/v1/knowledge/search", {"query": "发布流程", "limit": 4}),
        ("/api/v1/data/sql/validate", {"sql": "SELECT 1", "data_source_id": "source-1"}),
        (
            "/api/v1/data/sql/explain",
            {"sql": "SELECT 1 LIMIT 1", "data_source_id": "source-1"},
        ),
        ("/api/v1/admin/data/catalog", {}),
        ("/api/v1/admin/data/metrics", {"name": "paid_user_count"}),
        ("/api/v1/admin/data/dimensions", {"name": "paid_user_count"}),
        ("/api/v1/admin/data/entities", {"name": "paid_user_count"}),
        ("/api/v1/admin/data/relations", {"name": "paid_user_count"}),
        ("/api/v1/admin/data/rules", {"name": "paid_user_count"}),
        ("/api/v1/admin/data/time-definitions", {"name": "paid_user_count"}),
        ("/api/v1/admin/data/synonyms", {"name": "paid_user_count"}),
    ]


@pytest.mark.asyncio
async def test_client_exposes_versioned_evaluation_gates() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json=[{"status": "PASSED"}])
        return httpx.Response(201, json={"id": "evaluation-resource", "gate_passed": True})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        dataset = await client.create_evaluation_dataset(name="release-gate", domain="knowledge")
        assert dataset["id"] == "evaluation-resource"
        await client.add_evaluation_case(
            "dataset-1",
            {
                "external_id": "route-001",
                "evaluator": "ROUTING",
                "input_payload": {"question": "What is the policy?"},
                "expected": {"route": "KNOWLEDGE"},
            },
        )
        run = await client.run_evaluation(
            "dataset-1",
            {
                "agent_version_id": "agent-version-1",
                "model_profile_id": "profile-1",
                "application_revision": "revision-1",
                "minimum_pass_rate": 1.0,
            },
        )
        assert run["gate_passed"] is True
        assert (await client.list_evaluation_results("run-1"))[0]["status"] == "PASSED"

    assert requests == [
        (
            "POST",
            "/api/v1/admin/evaluations/datasets",
            {"name": "release-gate", "domain": "knowledge", "description": ""},
        ),
        (
            "POST",
            "/api/v1/admin/evaluations/datasets/dataset-1/cases",
            {
                "external_id": "route-001",
                "evaluator": "ROUTING",
                "input_payload": {"question": "What is the policy?"},
                "expected": {"route": "KNOWLEDGE"},
            },
        ),
        (
            "POST",
            "/api/v1/admin/evaluations/datasets/dataset-1/runs",
            {
                "agent_version_id": "agent-version-1",
                "model_profile_id": "profile-1",
                "application_revision": "revision-1",
                "minimum_pass_rate": 1.0,
            },
        ),
        ("GET", "/api/v1/admin/evaluations/runs/run-1/results", {}),
    ]


@pytest.mark.asyncio
async def test_client_exposes_governed_memory_and_run_snapshots() -> None:
    requests: list[tuple[str, str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, request.url.query.decode(), body))
        if request.url.path.endswith("/conversation") and request.method == "GET":
            return httpx.Response(200, json=[{"id": "conversation-snapshot"}])
        if request.url.path.endswith("/memories") and request.method == "GET":
            return httpx.Response(200, json=[{"id": "memory-snapshot"}])
        return httpx.Response(201, json={"id": "memory-1"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.create_memory(
            scope="WORKSPACE",
            owner_ref="workspace-1",
            content={"timezone": "UTC"},
        )
        assert (
            await client.list_memories(
                scope="WORKSPACE", owner_ref="workspace-1", status="APPROVED"
            )
        )[0]["id"] == "memory-snapshot"
        assert (await client.list_run_memories("run-1"))[0]["id"] == "memory-snapshot"
        assert (await client.list_run_conversation("run-1"))[0]["id"] == "conversation-snapshot"
        await client.decide_memory("memory-1", approve=True, reason="Governed preference")

    assert requests == [
        (
            "POST",
            "/api/v1/memories",
            "",
            {
                "scope": "WORKSPACE",
                "owner_ref": "workspace-1",
                "content": {"timezone": "UTC"},
                "sensitivity": "INTERNAL",
                "expires_at": None,
            },
        ),
        (
            "GET",
            "/api/v1/memories",
            "scope=WORKSPACE&owner_ref=workspace-1&status=APPROVED",
            {},
        ),
        ("GET", "/api/v1/runs/run-1/memories", "", {}),
        ("GET", "/api/v1/runs/run-1/conversation", "", {}),
        (
            "POST",
            "/api/v1/memories/memory-1/approve",
            "",
            {"reason": "Governed preference"},
        ),
    ]


@pytest.mark.asyncio
async def test_client_exposes_versioned_run_feedback_and_summary() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/summary"):
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "helpful": 1,
                    "needs_improvement": 0,
                    "helpful_rate": 1.0,
                },
            )
        return httpx.Response(200, json={"id": "feedback-1", "version": 1})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.get_run_feedback("run-1")
        await client.record_run_feedback(
            "run-1",
            rating="NEEDS_IMPROVEMENT",
            reason="Missing evidence",
            expected_version=2,
        )
        await client.get_feedback_summary()

    assert requests == [
        ("GET", "/api/v1/runs/run-1/feedback", {}),
        (
            "PUT",
            "/api/v1/runs/run-1/feedback",
            {
                "rating": "NEEDS_IMPROVEMENT",
                "reason": "Missing evidence",
                "expected_version": 2,
            },
        ),
        ("GET", "/api/v1/admin/feedback/summary", {}),
    ]


@pytest.mark.asyncio
async def test_client_exposes_metric_catalog_and_lineage() -> None:
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=[] if request.url.path.endswith("metrics") else {})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_metrics()
        await client.get_metric_lineage("metric-1")

    assert requests == [
        ("GET", "/api/v1/data/metrics"),
        ("GET", "/api/v1/data/lineage/metric-1"),
    ]


@pytest.mark.asyncio
async def test_client_exposes_versioned_workspace_tasks_and_decisions() -> None:
    requests: list[tuple[str, str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, request.url.query.decode(), body))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "collaboration-record"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.create_workspace_task(
            "workspace-1", {"title": "Verify impact", "priority": "CRITICAL"}
        )
        await client.list_workspace_tasks("workspace-1", status="OPEN", assignee_id="user-1")
        await client.update_workspace_task(
            "task-1", {"expected_version": 1, "status": "IN_PROGRESS"}
        )
        await client.create_workspace_decision(
            "workspace-1",
            {
                "title": "Use immutable evidence",
                "summary": "Preserve history",
                "rationale": "Required for replay",
            },
        )
        await client.decide_workspace_decision("decision-1", approve=True, expected_version=2)
        await client.list_workspace_decision_versions("decision-1")

    assert requests == [
        (
            "POST",
            "/api/v1/workspaces/workspace-1/tasks",
            "",
            {"title": "Verify impact", "priority": "CRITICAL"},
        ),
        (
            "GET",
            "/api/v1/workspaces/workspace-1/tasks",
            "status=OPEN&assignee_id=user-1&limit=200",
            {},
        ),
        (
            "PATCH",
            "/api/v1/workspace-tasks/task-1",
            "",
            {"expected_version": 1, "status": "IN_PROGRESS"},
        ),
        (
            "POST",
            "/api/v1/workspaces/workspace-1/decisions",
            "",
            {
                "title": "Use immutable evidence",
                "summary": "Preserve history",
                "rationale": "Required for replay",
            },
        ),
        (
            "POST",
            "/api/v1/workspace-decisions/decision-1/accept",
            "",
            {"expected_version": 2},
        ),
        (
            "GET",
            "/api/v1/workspace-decisions/decision-1/versions",
            "",
            {},
        ),
    ]


@pytest.mark.asyncio
async def test_client_uploads_and_downloads_artifacts() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
            assert b"release.txt" in request.content
            return httpx.Response(201, json={"id": "artifact-1"})
        return httpx.Response(200, content=b"release evidence")

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        artifact = await client.upload_artifact(
            "workspace-1",
            title="Release evidence",
            filename="release.txt",
            content=b"release evidence",
        )
        assert artifact["id"] == "artifact-1"
        assert await client.download_artifact("artifact-1") == b"release evidence"


@pytest.mark.asyncio
async def test_client_exposes_automation_lifecycle() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path.endswith("/workflows"):
            return httpx.Response(201, json={"workflow": {"id": "workflow-1"}})
        if request.url.path.endswith("/trigger"):
            return httpx.Response(202, json={"id": "execution-1"})
        if request.url.path.endswith("/review"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json={"status": "READ"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        created = await client.create_workflow(
            "workspace-1", {"name": "daily-watch", "spec": {"steps": []}}
        )
        assert created["workflow"]["id"] == "workflow-1"
        triggered = await client.trigger_workflow(
            "workflow-1",
            input_payload={"service": "payments"},
            idempotency_key="payments-2026-08-25",
        )
        assert triggered["id"] == "execution-1"
        reviewed = await client.review_automation_step(
            "step-1", decision="APPROVE", reason="Verified evidence"
        )
        assert reviewed["status"] == "COMPLETED"
        assert (await client.mark_notification_read("notification-1"))["status"] == "READ"

    assert requests == [
        (
            "POST",
            "/api/v1/workspaces/workspace-1/workflows",
            {"name": "daily-watch", "spec": {"steps": []}},
        ),
        (
            "POST",
            "/api/v1/workflows/workflow-1/trigger",
            {
                "input_payload": {"service": "payments"},
                "idempotency_key": "payments-2026-08-25",
            },
        ),
        (
            "POST",
            "/api/v1/automation/steps/step-1/review",
            {"decision": "APPROVE", "reason": "Verified evidence"},
        ),
        ("POST", "/api/v1/notifications/notification-1/read", {}),
    ]


@pytest.mark.asyncio
async def test_client_exposes_governed_action_lifecycle() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"id": "action-resource"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.create_action(
            "workspace-1",
            {
                "action_type": "CREATE_TICKET",
                "title": "Payment incident",
                "environment": "staging",
                "target": {"project_key": "OPS"},
                "parameters": {"summary": "Payment incident", "description": "Investigate"},
                "idempotency_key": "ticket-1",
            },
        )
        await client.preflight_action("action-1", reason="Evidence and rollback verified")
        await client.decide_action_approval(
            "approval-1", approve=True, reason="Approved by operator"
        )
        await client.request_action_rollback("action-1", reason="Close validation ticket")

    assert requests == [
        (
            "POST",
            "/api/v1/workspaces/workspace-1/actions",
            {
                "action_type": "CREATE_TICKET",
                "title": "Payment incident",
                "environment": "staging",
                "target": {"project_key": "OPS"},
                "parameters": {"summary": "Payment incident", "description": "Investigate"},
                "idempotency_key": "ticket-1",
            },
        ),
        (
            "POST",
            "/api/v1/actions/action-1/preflight",
            {"reason": "Evidence and rollback verified", "approval_ttl_minutes": 60},
        ),
        (
            "POST",
            "/api/v1/action-approvals/approval-1/approve",
            {"reason": "Approved by operator"},
        ),
        (
            "POST",
            "/api/v1/actions/action-1/rollback",
            {"reason": "Close validation ticket", "approval_ttl_minutes": 60},
        ),
    ]
