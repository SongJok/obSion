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
async def test_client_lists_and_decides_capability_approvals() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, str(request.url), body))
        return httpx.Response(200, json={"id": "approval-1", "status": "APPROVED"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_approvals(status="PENDING")
        await client.decide_approval("approval-1", approve=True, reason="Matches policy")

    assert requests == [
        (
            "GET",
            "https://obsion.example/api/v1/approvals?status=PENDING",
            {},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/approvals/approval-1/approve",
            {"reason": "Matches policy"},
        ),
    ]


@pytest.mark.asyncio
async def test_client_maps_im_senders_through_control_plane_identity() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/revoke"):
            return httpx.Response(200, json={"id": "binding-1", "active": False})
        if request.url.path.endswith("/im/messages"):
            return httpx.Response(202, json={"run_id": "run-1", "principal_id": "user-1"})
        return httpx.Response(201, json={"id": "binding-1", "sender_id": "alice-stable"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_im_bindings()
        await client.create_im_binding(
            channel="development", sender_id="alice-stable", user_id="user-1"
        )
        await client.revoke_im_binding("binding-1")
        await client.create_im_message(
            channel="development",
            sender_id="alice-stable",
            conversation_id="ops-room",
            text="你好",
            sender_display="Alice",
        )

    assert requests == [
        ("GET", "https://obsion.example/api/v1/admin/im-bindings", {}),
        (
            "POST",
            "https://obsion.example/api/v1/admin/im-bindings",
            {"channel": "development", "sender_id": "alice-stable", "user_id": "user-1"},
        ),
        ("POST", "https://obsion.example/api/v1/admin/im-bindings/binding-1/revoke", {}),
        (
            "POST",
            "https://obsion.example/api/v1/experience/im/messages",
            {
                "channel": "development",
                "sender_id": "alice-stable",
                "conversation_id": "ops-room",
                "text": "你好",
                "sender_display": "Alice",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_client_exposes_studio_registry_contracts() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json={"agents": [], "skills": []})
        return httpx.Response(200, json={"name": "studio-probe-agent", "version": 1})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_studio_catalog()
        await client.validate_studio_document("kind: Agent")
        await client.publish_studio_agent("kind: Agent")
        await client.publish_studio_skill("kind: Skill")
        await client.promote_studio_version(kind="Agent", name="studio-probe-agent", version=1)
        await client.rollback_studio_version(kind="Agent", name="studio-probe-agent", version=1)
        await client.compare_studio_versions(
            kind="Agent",
            name="studio-probe-agent",
            baseline_version=1,
            candidate_version=2,
        )

    assert requests == [
        ("GET", "https://obsion.example/api/v1/studio/catalog", {}),
        (
            "POST",
            "https://obsion.example/api/v1/studio/validate",
            {"document": "kind: Agent"},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/studio/agents",
            {"document": "kind: Agent"},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/studio/skills",
            {"document": "kind: Skill"},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/studio/promote",
            {"kind": "Agent", "name": "studio-probe-agent", "version": 1},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/studio/rollback",
            {"kind": "Agent", "name": "studio-probe-agent", "version": 1},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/studio/compare",
            {
                "kind": "Agent",
                "name": "studio-probe-agent",
                "baseline_version": 1,
                "candidate_version": 2,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_client_exposes_connector_and_capability_admin_contracts() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, str(request.url), body))
        return httpx.Response(200, json={"id": "created"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_connectors()
        await client.create_connector(
            {
                "name": "obsion-workflow-dispatch-test",
                "connector_type": "workflow-development",
                "environment": "development",
                "status": "ACTIVE",
                "declared_grants": ["automation.trigger"],
                "allowed_egress": [],
            }
        )
        await client.list_admin_capabilities()
        await client.list_operator_invocations(status="UNKNOWN", limit=25)
        await client.bind_capability(
            "capability-1",
            connector_id="connector-1",
            environment="development",
        )
        await client.probe_connector_health("connector-1")
        await client.discover_connector("connector-1")
        await client.scan_connector_plugin("connector-1")
        await client.promote_connector_plugin("connector-1")

    assert requests == [
        ("GET", "https://obsion.example/api/v1/admin/connectors", {}),
        (
            "POST",
            "https://obsion.example/api/v1/admin/connectors",
            {
                "name": "obsion-workflow-dispatch-test",
                "connector_type": "workflow-development",
                "environment": "development",
                "status": "ACTIVE",
                "declared_grants": ["automation.trigger"],
                "allowed_egress": [],
            },
        ),
        ("GET", "https://obsion.example/api/v1/admin/capabilities", {}),
        (
            "GET",
            "https://obsion.example/api/v1/admin/operator-invocations?limit=25&status=UNKNOWN",
            {},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/admin/capabilities/capability-1/bindings",
            {
                "connector_id": "connector-1",
                "environment": "development",
                "resource_selector": {},
            },
        ),
        ("POST", "https://obsion.example/api/v1/admin/connectors/connector-1/health", {}),
        ("POST", "https://obsion.example/api/v1/admin/connectors/connector-1/discover", {}),
        ("POST", "https://obsion.example/api/v1/admin/connectors/connector-1/scan", {}),
        ("POST", "https://obsion.example/api/v1/admin/connectors/connector-1/promote", {}),
    ]


@pytest.mark.asyncio
async def test_client_exposes_eval_console_contracts() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, str(request.url), body))
        return httpx.Response(200, json={"datasets": [], "gate_passed": True})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.list_eval_catalog()
        await client.create_eval_dataset(name="routing", domain="foundation")
        await client.add_eval_case(
            "dataset-1",
            {
                "external_id": "route-001",
                "evaluator": "ROUTING",
                "input_payload": {"question": "What is the policy?"},
                "expected": {"route": "KNOWLEDGE"},
            },
        )
        await client.start_eval_run(
            "dataset-1",
            {
                "agent_version_id": "agent-1",
                "model_profile_id": "profile-1",
                "application_revision": "rev-1",
            },
        )
        await client.compare_eval_runs(baseline_run_id="run-1", candidate_run_id="run-2")

    assert requests == [
        ("GET", "https://obsion.example/api/v1/eval/catalog", {}),
        (
            "POST",
            "https://obsion.example/api/v1/eval/datasets",
            {"name": "routing", "domain": "foundation", "description": ""},
        ),
        (
            "POST",
            "https://obsion.example/api/v1/eval/datasets/dataset-1/cases",
            {
                "external_id": "route-001",
                "evaluator": "ROUTING",
                "input_payload": {"question": "What is the policy?"},
                "expected": {"route": "KNOWLEDGE"},
            },
        ),
        (
            "POST",
            "https://obsion.example/api/v1/eval/datasets/dataset-1/runs",
            {
                "agent_version_id": "agent-1",
                "model_profile_id": "profile-1",
                "application_revision": "rev-1",
            },
        ),
        (
            "POST",
            "https://obsion.example/api/v1/eval/compare",
            {"baseline_run_id": "run-1", "candidate_run_id": "run-2"},
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
        if request.url.path == "/api/v1/knowledge/sources/feishu/documents":
            return httpx.Response(
                201,
                json={
                    "document": {"id": "doc-feishu", "title": "Feishu SOP"},
                    "source": "feishu",
                    "chunk_count": 2,
                },
            )
        if request.url.path == "/api/v1/code/repositories" and request.method == "GET":
            return httpx.Response(200, json=[{"name": "payment-service"}])
        if request.url.path == "/api/v1/code/repositories":
            return httpx.Response(201, json={"id": "repo-1", "name": "payment-service"})
        if request.url.path.endswith("/snapshots"):
            return httpx.Response(201, json={"snapshot": {"id": "snap-1"}})
        if request.url.path == "/api/v1/code/symbols/search":
            return httpx.Response(200, json=[{"kind": "CLASS", "qualified_name": "OrderService"}])
        return httpx.Response(200, json=[{"document_id": "doc-1"}])

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        assert (await client.list_capabilities())[0]["name"] == "knowledge.search"
        data = await client.query_data("thread-1", "上周收入是多少？")
        assert data["run"]["id"] == "run-1"
        assert (await client.search_knowledge("发布流程", limit=4))[0]["document_id"] == "doc-1"
        ingested = await client.ingest_feishu_document("doxcnPhase64Token")
        assert ingested["source"] == "feishu"
        assert (await client.list_code_repositories())[0]["name"] == "payment-service"
        created = await client.create_code_repository(name="payment-service")
        assert created["id"] == "repo-1"
        indexed = await client.index_code_snapshot(
            "repo-1",
            commit_id="abc1234",
            files=[{"path": "src/app.py", "content": "def ping():\n    return 1\n"}],
        )
        assert indexed["snapshot"]["id"] == "snap-1"
        assert (await client.search_code_symbols("OrderService", limit=8))[0]["kind"] == "CLASS"
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
        (
            "/api/v1/knowledge/sources/feishu/documents",
            {
                "document_id": "doxcnPhase64Token",
                "obj_type": "auto",
                "classification": "INTERNAL",
                "acl": {"organization": True},
                "inherit_acl": False,
            },
        ),
        ("/api/v1/code/repositories", {}),
        (
            "/api/v1/code/repositories",
            {
                "name": "payment-service",
                "classification": "INTERNAL",
                "acl": {"organization": True},
                "default_branch": "main",
            },
        ),
        (
            "/api/v1/code/repositories/repo-1/snapshots",
            {
                "commit_id": "abc1234",
                "files": [{"path": "src/app.py", "content": "def ping():\n    return 1\n"}],
            },
        ),
        ("/api/v1/code/symbols/search", {"query": "OrderService", "limit": 8}),
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
async def test_client_exposes_feishu_wiki_space_sync() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.url.path, body))
        if request.url.path == "/api/v1/knowledge/sources/feishu/spaces":
            return httpx.Response(200, json=[{"space_id": "7365887123", "name": "Ops"}])
        if request.url.path.endswith("/nodes"):
            return httpx.Response(
                200,
                json=[{"node_token": "wikcn1", "obj_type": "docx", "obj_token": "doxcn1"}],
            )
        return httpx.Response(
            201,
            json={"operation": "knowledge.sync", "space_id": "7365887123", "ingested_count": 1},
        )

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        assert (await client.list_feishu_spaces())[0]["space_id"] == "7365887123"
        assert (await client.list_feishu_wiki_nodes("7365887123"))[0]["obj_type"] == "docx"
        synced = await client.sync_feishu_space("7365887123")
        assert synced["operation"] == "knowledge.sync"

    assert requests == [
        ("/api/v1/knowledge/sources/feishu/spaces", {}),
        ("/api/v1/knowledge/sources/feishu/spaces/7365887123/nodes", {}),
        (
            "/api/v1/knowledge/sources/feishu/spaces/7365887123/sync",
            {
                "classification": "INTERNAL",
                "acl": {"organization": True},
                "inherit_acl": False,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_client_exposes_confluence_knowledge() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.url.path, body))
        if request.url.path == "/api/v1/knowledge/sources/confluence/spaces":
            return httpx.Response(200, json=[{"space_id": "111222333", "key": "OPS"}])
        if request.url.path.endswith("/sync"):
            return httpx.Response(
                201,
                json={"operation": "knowledge.sync", "space_id": "111222333", "ingested_count": 1},
            )
        return httpx.Response(
            201,
            json={"document": {"id": "doc-1", "title": "SOP"}, "source": "confluence"},
        )

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        ingested = await client.ingest_confluence_page("4567890123")
        assert ingested["source"] == "confluence"
        assert (await client.list_confluence_spaces())[0]["key"] == "OPS"
        synced = await client.sync_confluence_space("111222333")
        assert synced["operation"] == "knowledge.sync"

    assert requests == [
        (
            "/api/v1/knowledge/sources/confluence/pages",
            {
                "page_id": "4567890123",
                "classification": "INTERNAL",
                "acl": {"organization": True},
                "inherit_acl": False,
            },
        ),
        ("/api/v1/knowledge/sources/confluence/spaces", {}),
        (
            "/api/v1/knowledge/sources/confluence/spaces/111222333/sync",
            {
                "classification": "INTERNAL",
                "acl": {"organization": True},
                "inherit_acl": False,
            },
        ),
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
        if request.method in {"GET", "PATCH", "DELETE"} and request.url.path.endswith(
            "/memories/memory-1"
        ):
            return httpx.Response(200, json={"id": "memory-1", "status": "CANDIDATE"})
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
        await client.get_memory("memory-1")
        await client.update_memory("memory-1", content={"timezone": "UTC"})
        await client.revoke_memory("memory-1")

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
        ("GET", "/api/v1/memories/memory-1", "", {}),
        (
            "PATCH",
            "/api/v1/memories/memory-1",
            "",
            {"content": {"timezone": "UTC"}},
        ),
        ("DELETE", "/api/v1/memories/memory-1", "", {}),
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
        if request.url.path.endswith("/slo"):
            return httpx.Response(200, json={"source": "postgresql", "runs": {"success_rate": 1.0}})
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
        await client.get_runtime_slo()

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
        ("GET", "/api/v1/admin/slo", {}),
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
            assert b"/releases/notes.txt" in request.content
            return httpx.Response(201, json={"id": "artifact-1"})
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[{"id": "artifact-1"}])
        if request.url.path.endswith("/reports"):
            return httpx.Response(200, json=[{"id": "report-1"}])
        if request.url.path.endswith("/dashboards"):
            return httpx.Response(200, json=[{"id": "dashboard-1"}])
        if request.url.path.endswith("/sql"):
            return httpx.Response(200, json=[{"id": "sql-1"}])
        if request.url.path.endswith("/evidence"):
            return httpx.Response(200, json=[{"id": "evidence-1"}])
        if request.url.path.endswith("/timeline"):
            return httpx.Response(200, json=[{"id": "event-1"}])
        return httpx.Response(200, content=b"release evidence")

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        artifact = await client.upload_artifact(
            "workspace-1",
            title="Release evidence",
            filename="release.txt",
            content=b"release evidence",
            path="/releases/notes.txt",
        )
        assert artifact["id"] == "artifact-1"
        assert await client.download_artifact("artifact-1") == b"release evidence"
        assert await client.list_workspace_files("workspace-1") == [{"id": "artifact-1"}]
        assert await client.list_workspace_reports("workspace-1") == [{"id": "report-1"}]
        assert await client.list_workspace_dashboards("workspace-1") == [{"id": "dashboard-1"}]
        assert await client.list_workspace_sql("workspace-1") == [{"id": "sql-1"}]
        assert await client.list_workspace_evidence("workspace-1") == [{"id": "evidence-1"}]
        assert await client.list_workspace_timeline("workspace-1") == [{"id": "event-1"}]


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


@pytest.mark.asyncio
async def test_client_records_governed_im_delivery_receipts() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"id": "delivery-1", "status": "PENDING"})

    async with AsyncObsionClient(
        "https://obsion.example", transport=httpx.MockTransport(handler)
    ) as client:
        await client.prepare_im_delivery("run-1")
        await client.complete_im_delivery(
            "delivery-1",
            vendor_message_id="om_1",
        )
        await client.fail_im_delivery("delivery-1")

    assert requests == [
        (
            "POST",
            "/api/v1/experience/im/runs/run-1/deliveries",
            {},
        ),
        (
            "POST",
            "/api/v1/experience/im/deliveries/delivery-1/complete",
            {"vendor_message_id": "om_1"},
        ),
        (
            "POST",
            "/api/v1/experience/im/deliveries/delivery-1/fail",
            {"failure_code": "vendor_request_failed"},
        ),
    ]
