from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from obsion.capabilities.connectors import HttpJsonExecutor
from obsion.capabilities.dingtalk_docs import DingTalkDocsDeniedError, DingTalkDocsResponseError
from obsion.capabilities.dingtalk_wiki import DingTalkWikiClient
from obsion.common.errors import ValidationError
from obsion.db.models import Connector
from obsion.db.session import Database
from obsion.knowledge.connector_contract import KnowledgeConnectorBudget, SyncBudgetTracker


def _space(**overrides: Any) -> dict[str, Any]:
    return {
        "workspaceId": "wiki_test",
        "corpId": "corp_test",
        "type": "TEAM",
        "name": "Company knowledge",
        "rootNodeId": "root_node",
        "permissionRole": "OWNER",
        **overrides,
    }


def _node(node_id: str = "document_one", **overrides: Any) -> dict[str, Any]:
    return {
        "workspaceId": "wiki_test",
        "nodeId": node_id,
        "name": "Source title",
        "type": "FILE",
        "extension": "adoc",
        "hasChildren": False,
        "permissionRole": "READER",
        **overrides,
    }


def _client(handler: Any) -> DingTalkWikiClient:
    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.dingtalk.com"
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "test-only-token", "expireIn": 7200})
        assert request.headers["x-acs-dingtalk-access-token"] == "test-only-token"
        assert request.url.params["operatorId"] == "operator_union_test"
        assert request.url.params["withPermissionRole"] == "true"
        return httpx.Response(200, json=handler(request))

    return DingTalkWikiClient(
        corp_id="corp_test",
        operator_id="operator_union_test",
        app_key="test-app",
        app_secret="test-secret",
        transport=httpx.MockTransport(respond),
    )


async def test_wiki_pagination_keeps_empty_page_cursor_and_filters_foreign_and_personal() -> None:
    calls: list[str | None] = []

    def respond(request: httpx.Request) -> dict[str, Any]:
        assert request.url.path == "/v2.0/wiki/workspaces"
        assert request.url.params["maxResults"] == "30"
        cursor = request.url.params.get("nextToken")
        calls.append(cursor)
        if cursor is None:
            return {"workspaces": [], "nextToken": " opaque / +cursor= "}
        return {
            "workspaces": [
                _space(),
                _space(workspaceId="foreign", corpId="other_corp"),
                _space(workspaceId="private", type="PERSONAL"),
            ]
        }

    client = _client(respond)
    try:
        spaces = await client.list_workspaces()
    finally:
        await client.aclose()
    assert [x.workspace_id for x in spaces] == ["wiki_test"]
    assert calls == [None, " opaque / +cursor= "]


async def test_wiki_recurses_with_root_binding_and_preserves_document_types() -> None:
    calls: list[tuple[str, str | None]] = []

    def respond(request: httpx.Request) -> dict[str, Any]:
        if request.url.path == "/v2.0/wiki/workspaces":
            return {"workspaces": [_space()]}
        assert request.url.path == "/v2.0/wiki/nodes"
        assert request.url.params["maxResults"] == "50"
        parent = request.url.params["parentNodeId"]
        cursor = request.url.params.get("nextToken")
        calls.append((parent, cursor))
        if parent == "root_node":
            return {"nodes": [_node("folder_one", type="FOLDER", extension=None, hasChildren=True)]}
        if cursor is None:
            return {"nodes": [_node()], "nextToken": "second"}
        return {"nodes": [_node("table_one", extension="able")]}

    tracker = SyncBudgetTracker(KnowledgeConnectorBudget())
    client = _client(respond)
    try:
        nodes = await client.list_workspace_nodes("wiki_test", tracker=tracker)
    finally:
        await client.aclose()
    assert [n.node_type for n in nodes] == ["folder", "adoc", "able"]
    assert nodes[0].document_id == ""
    assert calls == [("root_node", None), ("folder_one", None), ("folder_one", "second")]
    assert tracker.snapshot().pages_used == 4
    assert tracker.snapshot().nodes_used == 3
    assert tracker.snapshot().depth_used == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"workspaces": None},
        {"workspaces": [None]},
        {"workspaces": [], "success": False},
        {"workspaces": [], "success": 0},
        {"workspaces": [], "success": "true"},
        {"workspaces": [], "success": None},
        {"workspaces": [], "nextToken": 123},
        {"workspaces": [], "nextToken": " "},
        {"workspaces": [], "hasMore": True},
        {"workspaces": [], "nextToken": "more", "hasMore": False},
        {"workspaces": [_space(corpId=None)]},
        {"workspaces": [_space(type="UNKNOWN")]},
        {"workspaces": [_space(), _space()]},
    ],
)
async def test_wiki_incomplete_or_malformed_discovery_never_looks_complete(
    payload: dict[str, Any],
) -> None:
    client = _client(lambda _: payload)
    try:
        with pytest.raises(DingTalkDocsResponseError):
            await client.list_workspaces()
    finally:
        await client.aclose()


async def test_wiki_repeated_cursor_fails_before_unbounded_requests() -> None:
    tracker = SyncBudgetTracker(KnowledgeConnectorBudget())
    client = _client(lambda _: {"workspaces": [], "nextToken": "loop"})
    try:
        with pytest.raises(DingTalkDocsResponseError):
            await client.list_workspaces(tracker=tracker)
    finally:
        await client.aclose()
    assert tracker.snapshot().pages_used == 2


@pytest.mark.parametrize("scope", [{"corpId": "foreign"}, {"type": "PERSONAL"}])
async def test_wiki_caller_cannot_select_an_out_of_scope_workspace(scope: dict[str, str]) -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> dict[str, Any]:
        requests.append(request.url.path)
        return {"workspaces": [_space(**scope)]}

    client = _client(respond)
    try:
        with pytest.raises(DingTalkDocsDeniedError):
            await client.list_workspace_nodes("wiki_test")
    finally:
        await client.aclose()
    assert requests == ["/v2.0/wiki/workspaces"]


@pytest.mark.parametrize("changes", [{"workspaceId": "foreign"}, {"permissionRole": "NONE"}])
async def test_wiki_nodes_cannot_escape_workspace_or_permission(changes: dict[str, str]) -> None:
    client = _client(
        lambda r: (
            {"workspaces": [_space()]}
            if r.url.path.endswith("workspaces")
            else {"nodes": [_node(**changes)]}
        )
    )
    try:
        with pytest.raises(DingTalkDocsDeniedError):
            await client.list_workspace_nodes("wiki_test")
    finally:
        await client.aclose()


@pytest.mark.parametrize("dimension", ["pages", "nodes", "depth"])
async def test_wiki_traversal_obeys_shared_sync_budgets(dimension: str) -> None:
    tracker = SyncBudgetTracker(KnowledgeConnectorBudget(**{"max_" + dimension: 1}))
    client = _client(
        lambda r: (
            {"workspaces": [_space()]}
            if r.url.path.endswith("workspaces")
            else {
                "nodes": [
                    _node("folder_one", type="FOLDER", hasChildren=True),
                    *([_node()] if dimension == "nodes" else []),
                ]
            }
        )
    )
    try:
        with pytest.raises(ValidationError) as caught:
            await client.list_workspace_nodes("wiki_test", tracker=tracker)
        assert caught.value.code == "knowledge_sync_budget_exceeded"
        assert caught.value.details["dimension"] == dimension
    finally:
        await client.aclose()


async def test_wiki_cycle_never_retraverses_root() -> None:
    client = _client(
        lambda r: (
            {"workspaces": [_space()]}
            if r.url.path.endswith("workspaces")
            else {"nodes": [_node("root_node", type="FOLDER", hasChildren=True)]}
        )
    )
    try:
        with pytest.raises(DingTalkDocsResponseError):
            await client.list_workspace_nodes("wiki_test")
    finally:
        await client.aclose()


async def test_wiki_node_ids_are_not_sent_to_legacy_content_endpoints() -> None:
    client = _client(
        lambda _: pytest.fail("Discovery is not authorization to fetch legacy content")
    )
    try:
        with pytest.raises(ValidationError) as caught:
            await client.fetch_document(document_id="document_one")
        assert caught.value.code == "dingtalk_docs_operation_invalid"
    finally:
        await client.aclose()


@pytest.mark.parametrize("corp,operator", [("", "user"), ("corp", " ")])
def test_wiki_requires_explicit_organization_and_operator(corp: str, operator: str) -> None:
    with pytest.raises(ValidationError):
        DingTalkWikiClient(corp_id=corp, operator_id=operator, app_key="key", app_secret="test")


def test_wiki_rest_uses_configured_protocol_gateway_policy_and_audit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OBSION_DINGTALK_APP_KEY", "test-app")
    monkeypatch.setenv("OBSION_DINGTALK_APP_SECRET", "test-secret")

    async def configure() -> None:
        database = Database(client.app.state.settings)
        try:
            async with database.sessions() as session:
                connector = await session.scalar(
                    select(Connector).where(Connector.name == "obsion-dingtalk-docs")
                )
                assert connector is not None
                connector.configuration = {
                    **connector.configuration,
                    "protocol": "dingtalk.wiki.v2",
                    "corp_id": "corp_test",
                    "operator_id": "operator_union_test",
                }
                await session.commit()
        finally:
            await database.dispose()

    asyncio.run(configure())
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "test-token", "expireIn": 7200})
        assert request.url.params["operatorId"] == "operator_union_test"
        if request.url.path == "/v2.0/wiki/workspaces":
            return httpx.Response(200, json={"workspaces": [_space()]})
        assert request.url.path == "/v2.0/wiki/nodes"
        return httpx.Response(200, json={"nodes": [_node()]})

    gateway = client.app.state.capability_gateway
    executor = gateway.executors["HTTP"]
    assert isinstance(executor, HttpJsonExecutor)
    monkeypatch.setattr(executor, "transport", httpx.MockTransport(respond))
    correlation_id = str(uuid4())
    response = client.get(
        "/api/v1/knowledge/sources/dingtalk/workspaces/wiki_test/nodes",
        headers={"X-Request-ID": correlation_id},
    )
    assert response.status_code == 200, response.text
    assert "document_one" in response.text
    assert "test-secret" not in response.text
    assert requests == ["/v1.0/oauth2/accessToken", "/v2.0/wiki/workspaces", "/v2.0/wiki/nodes"]
    audit = client.get("/api/v1/admin/audit?limit=100").json()
    record = next(item for item in audit if item["correlation_id"] == correlation_id)
    assert record["outcome"] == "SUCCESS"
    assert record["policy_decision_id"] is not None
    assert record["metadata"]["capability"] == "knowledge.source.items"

    # The same source cannot silently use an old content API or report an
    # unsupported sync as a successful empty import.
    response = client.post(
        "/api/v1/knowledge/sources/dingtalk/documents",
        json={"document_id": "document_one", "acl": {"organization": True}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "dingtalk_docs_operation_invalid"
    assert len(requests) == 3
    response = client.post(
        "/api/v1/knowledge/sources/dingtalk/workspaces/wiki_test/sync",
        json={"inherit_acl": True},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "dingtalk_docs_operation_invalid"
    assert len(requests) == 3
