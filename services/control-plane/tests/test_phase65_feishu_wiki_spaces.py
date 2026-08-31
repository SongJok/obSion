from __future__ import annotations

import ast
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.capabilities.feishu_docs import (
    FEISHU_ORIGIN,
    FeishuDocsClient,
    FeishuDocsDeniedError,
    FeishuDocument,
    FeishuWikiNode,
    normalize_space_id,
)
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector, Document, DocumentVersion
from obsion.domain.enums import Classification, ConnectorStatus
from obsion.security.identity import Principal

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run_id}")


def _feishu_document() -> FeishuDocument:
    return FeishuDocument(
        document_id="doxcnPhase65Rollback",
        title="Wiki Rollback SOP",
        content="# Wiki Rollback SOP\nEvery wiki SOP requires an owner and rollback plan.",
        revision_id="7",
        obj_type="docx",
        wiki_token=None,
    )


def _connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="obsion-feishu-docs",
        connector_type="feishu-docs",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint=FEISHU_ORIGIN,
        configuration={"protocol": "feishu.docs.v1", "app_id_env": "OBSION_FEISHU_APP_ID"},
        credential_ref="env://OBSION_FEISHU_APP_SECRET",
        declared_grants=["knowledge.write"],
        allowed_egress=["https://open.feishu.cn"],
    )


def test_feishu_space_id_fails_closed() -> None:
    with pytest.raises(ValidationError) as caught:
        normalize_space_id("x")
    assert caught.value.code == "feishu_docs_space_id_invalid"
    assert normalize_space_id("7365887123") == "7365887123"


@pytest.mark.asyncio
async def test_feishu_docs_client_paginates_spaces_and_walks_child_nodes() -> None:
    seen: list[str] = []

    async def responder(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/tenant_access_token/internal/"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-test", "expire": 3600}
            )
        if request.url.path == "/open-apis/wiki/v2/spaces":
            page = request.url.params.get("page_token")
            if not page:
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "items": [{"space_id": 7365887123, "name": "Ops", "description": ""}],
                            "has_more": True,
                            "page_token": "next-space",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [{"space_id": "7365887999", "name": "Legal"}],
                        "has_more": False,
                    },
                },
            )
        if request.url.path == "/open-apis/wiki/v2/spaces/7365887123/nodes":
            parent = request.url.params.get("parent_node_token")
            if parent == "wikcnPhase65Folder":
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "items": [
                                {
                                    "node_token": "wikcnPhase65Doc",
                                    "obj_token": "doxcnPhase65Rollback",
                                    "obj_type": "docx",
                                    "title": "Wiki Rollback SOP",
                                },
                                {
                                    "node_token": "wikcnPhase65Sheet",
                                    "obj_token": "shtcnPhase65Sheet",
                                    "obj_type": "sheet",
                                    "title": "Sheet",
                                },
                            ],
                            "has_more": False,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "node_token": "wikcnPhase65Folder",
                                "obj_token": "wikcnPhase65Folder",
                                "obj_type": "origin",
                                "title": "SOPs",
                                "has_child": True,
                            }
                        ],
                        "has_more": False,
                    },
                },
            )
        return httpx.Response(404, json={"code": 1, "msg": "missing"})

    client = FeishuDocsClient(
        app_id="cli_test",
        app_secret="secret_test",
        transport=httpx.MockTransport(responder),
    )
    try:
        spaces = await client.list_spaces()
        nodes = await client.list_nodes("7365887123")
    finally:
        await client.aclose()
    assert [space.space_id for space in spaces] == ["7365887123", "7365887999"]
    assert [node.obj_type for node in nodes] == ["origin", "docx", "sheet"]
    assert any(path.endswith("/spaces/7365887123/nodes") for path in seen)


@pytest.mark.asyncio
async def test_feishu_docs_executor_syncs_a_wiki_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "cli_test")
    document = Document(
        id=uuid4(),
        organization_id=uuid4(),
        source="feishu",
        external_id="doxcnPhase65Rollback",
        title="Wiki Rollback SOP",
        classification=Classification.INTERNAL,
        acl={"organization": True},
        current_version=1,
    )
    version = DocumentVersion(
        id=uuid4(),
        organization_id=document.organization_id,
        document_id=document.id,
        version=1,
        media_type="text/plain",
        extracted_text="body",
        checksum_sha256="abc",
        parser_version="text-v1",
        metadata_json={},
    )

    class _Service:
        async def ingest(
            self, *args: object, **kwargs: object
        ) -> tuple[Document, DocumentVersion, int]:
            del args, kwargs
            return document, version, 2

    async def fake_nodes(*args: object, **kwargs: object) -> list[FeishuWikiNode]:
        del args, kwargs
        return [
            FeishuWikiNode(
                space_id="7365887123",
                node_token="wikcnPhase65Doc",
                obj_token="doxcnPhase65Rollback",
                obj_type="docx",
                title="Wiki Rollback SOP",
            ),
            FeishuWikiNode(
                space_id="7365887123",
                node_token="wikcnPhase65Sheet",
                obj_token="shtcnPhase65Sheet",
                obj_type="sheet",
                title="Sheet",
            ),
        ]

    async def fake_fetch(**kwargs: object) -> FeishuDocument:
        del kwargs
        return _feishu_document()

    monkeypatch.setattr("obsion.knowledge.feishu.list_feishu_nodes", fake_nodes)
    monkeypatch.setattr(
        "obsion.knowledge.feishu.fetch_authorized_feishu_document",
        fake_fetch,
    )
    connector = _connector()
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST),
        knowledge_service=_Service(),
    )
    result = await executor.invoke(
        connector,
        {
            "operation": "knowledge.sync",
            "space_id": "7365887123",
            "acl": {"organization": True},
        },
        "secret_test",
        ConnectorContext(
            principal=Principal(
                id=uuid4(),
                organization_id=connector.organization_id,
                external_id="phase65-user",
                display_name="Phase 65",
                permissions=frozenset({"knowledge.write"}),
            ),
            run_id=uuid4(),
            step_id=None,
            session=object(),  # type: ignore[arg-type]
        ),
    )
    assert result.data["operation"] == "knowledge.sync"
    assert result.data["ingested_count"] == 1
    assert result.data["skipped_count"] == 1
    assert result.data["failed_count"] == 0
    assert result.data["skipped"][0]["reason"] == "feishu_docs_obj_type_unsupported"


def test_feishu_space_sync_enters_knowledge_pipeline_and_harness_citations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "secret_test")

    async def fake_nodes(*args: object, **kwargs: object) -> list[FeishuWikiNode]:
        del args, kwargs
        return [
            FeishuWikiNode(
                space_id="7365887123",
                node_token="wikcnPhase65Doc",
                obj_token="doxcnPhase65Rollback",
                obj_type="docx",
                title="Wiki Rollback SOP",
            ),
            FeishuWikiNode(
                space_id="7365887123",
                node_token="wikcnPhase65Sheet",
                obj_token="shtcnPhase65Sheet",
                obj_type="sheet",
                title="Sheet",
            ),
        ]

    async def fake_fetch(**kwargs: object) -> FeishuDocument:
        del kwargs
        return _feishu_document()

    monkeypatch.setattr("obsion.knowledge.feishu.list_feishu_nodes", fake_nodes)
    monkeypatch.setattr(
        "obsion.knowledge.feishu.fetch_authorized_feishu_document",
        fake_fetch,
    )
    synced = client.post(
        "/api/v1/knowledge/sources/feishu/spaces/7365887123/sync",
        json={"acl": {"organization": True}},
    )
    assert synced.status_code == 201, synced.text
    payload = synced.json()
    assert payload["operation"] == "knowledge.sync"
    assert payload["ingested_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["ingested"][0]["source"] == "feishu"
    assert payload["ingested"][0]["external_id"] == "doxcnPhase65Rollback"

    search = client.post(
        "/api/v1/knowledge/search",
        json={"query": "wiki rollback plan", "limit": 8},
    )
    assert search.status_code == 200, search.text
    assert search.json()[0]["source"] == "feishu"

    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Feishu Wiki Knowledge", "description": "Phase 65"},
    )
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Feishu Wiki QA"},
    )
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "What does the wiki rollback SOP require?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    assert run["intent"]["route"] == "KNOWLEDGE"
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    assert artifacts[0]["inline_content"]["citations"]


def test_feishu_space_list_and_sync_require_credentials_and_valid_ids(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    missing = client.get("/api/v1/knowledge/sources/feishu/spaces")
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == "credential_unavailable"

    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "secret_test")
    invalid = client.post(
        "/api/v1/knowledge/sources/feishu/spaces/x/sync",
        json={"acl": {"organization": True}},
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["code"] == "feishu_docs_space_id_invalid"


def test_feishu_wiki_sync_is_capability_not_experience() -> None:
    knowledge = (WEB_ROOT / "src" / "components" / "knowledge-view.tsx").read_text(encoding="utf-8")
    assert "同步飞书知识库" in knowledge
    source = ast.parse(
        (SOURCE_ROOT / "capabilities" / "feishu_docs.py").read_text(encoding="utf-8")
    )
    imports = [
        node.module
        for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.harness.runtime" not in imports
    assert "obsion_im.feishu" not in imports
    assert "obsion.application.im_delivery" not in imports


@pytest.mark.skipif(
    os.environ.get("OBSION_FEISHU_LIVE") != "1",
    reason="Live Feishu wiki listing is operator-owned",
)
@pytest.mark.asyncio
@pytest.mark.live
async def test_feishu_wiki_live_list_fails_closed_without_scope() -> None:
    app_id = os.environ.get("OBSION_FEISHU_APP_ID", "")
    app_secret = os.environ.get("OBSION_FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        pytest.skip("Live Feishu credentials are not available")
    client = FeishuDocsClient(app_id=app_id, app_secret=app_secret)
    try:
        health = await client.health()
        assert health["authenticated"] is True
        try:
            spaces = await client.list_spaces()
        except (FeishuDocsDeniedError, ValidationError):
            return
        assert isinstance(spaces, list)
    finally:
        await client.aclose()
