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
async def test_client_exposes_governed_data_and_knowledge_flows() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.url.path, body))
        if request.url.path == "/api/v1/capabilities":
            return httpx.Response(200, json=[{"name": "knowledge.search"}])
        if request.url.path == "/api/v1/data/query":
            return httpx.Response(202, json={"turn": {"id": "turn-1"}, "run": {"id": "run-1"}})
        return httpx.Response(200, json=[{"document_id": "doc-1"}])

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        assert (await client.list_capabilities())[0]["name"] == "knowledge.search"
        data = await client.query_data("thread-1", "上周收入是多少？")
        assert data["run"]["id"] == "run-1"
        assert (await client.search_knowledge("发布流程", limit=4))[0]["document_id"] == "doc-1"

    assert requests == [
        ("/api/v1/capabilities", {}),
        ("/api/v1/data/query", {"thread_id": "thread-1", "question": "上周收入是多少？"}),
        ("/api/v1/knowledge/search", {"query": "发布流程", "limit": 4}),
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
