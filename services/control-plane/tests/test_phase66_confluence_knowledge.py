from __future__ import annotations

import ast
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.confluence import (
    ConfluenceClient,
    ConfluenceDeniedError,
    ConfluencePage,
    ConfluenceResponseError,
    ConfluenceSpacePage,
    assert_confluence_egress,
    merge_restriction_members,
    normalize_page_id,
    normalize_site_host,
)
from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector, Document, DocumentVersion
from obsion.domain.enums import Classification, ConnectorStatus
from obsion.knowledge.confluence import resolve_ingest_acl
from obsion.security.identity import Principal

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion"
SITE_HOST = "example.atlassian.net"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run_id}")


def _page() -> ConfluencePage:
    return ConfluencePage(
        page_id="4567890123",
        title="Confluence Rollback SOP",
        content=(
            "<h1>Rollback SOP</h1><p>Every Confluence SOP requires an owner and rollback plan.</p>"
        ),
        version="4",
        space_id="111222333",
    )


def _connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="obsion-confluence",
        connector_type="confluence",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint=f"https://{SITE_HOST}",
        configuration={
            "protocol": "confluence.cloud.v2",
            "email_env": "OBSION_CONFLUENCE_EMAIL",
            "site_host": SITE_HOST,
        },
        credential_ref="env://OBSION_CONFLUENCE_API_TOKEN",
        declared_grants=["knowledge.write"],
        allowed_egress=[f"https://{SITE_HOST}"],
    )


def test_confluence_ids_site_and_acl_fail_closed() -> None:
    with pytest.raises(ValidationError) as caught:
        normalize_page_id("0")
    assert caught.value.code == "confluence_page_id_invalid"
    with pytest.raises(ValidationError) as caught:
        normalize_site_host("evil.example")
    assert caught.value.code == "confluence_site_invalid"
    with pytest.raises(ValidationError) as caught:
        normalize_site_host("localhost.atlassian.net")
    assert caught.value.code == "confluence_site_invalid"
    assert normalize_site_host("Acme.atlassian.net") == "acme.atlassian.net"
    with pytest.raises(ValidationError) as caught:
        resolve_ingest_acl(requested=None, inherited=None, inherit_acl=False)
    assert caught.value.code == "document_acl_required"


def test_confluence_restrictions_never_invent_organization_acl() -> None:
    assert merge_restriction_members({"read": {"restrictions": {"user": {"results": []}}}}) is None
    mapped = merge_restriction_members(
        {
            "read": {
                "restrictions": {
                    "user": {"results": [{"accountId": "acc-owner"}]},
                    "group": {"results": [{"name": "engineering"}]},
                }
            }
        }
    )
    assert mapped is not None
    assert mapped["organization"] is False
    assert mapped["users"] == ["acc-owner"]
    assert mapped["roles"] == ["engineering"]


def test_confluence_egress_is_pinned_to_the_cloud_site() -> None:
    connector = _connector()
    assert_confluence_egress(connector)
    connector.endpoint = "https://evil.example"
    with pytest.raises(ValidationError) as caught:
        assert_confluence_egress(connector)
    assert caught.value.code == "connector_egress_denied"
    connector.endpoint = f"https://{SITE_HOST}"
    connector.configuration = {
        "protocol": "confluence.cloud.v2",
        "site_host": SITE_HOST,
        "base_url": "https://evil.example",
    }
    with pytest.raises(ValidationError) as caught:
        assert_confluence_egress(connector)
    assert caught.value.code == "confluence_operation_invalid"


@pytest.mark.asyncio
async def test_confluence_client_fetches_page_and_paginates_spaces() -> None:
    seen: list[str] = []

    async def responder(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/wiki/api/v2/pages/4567890123":
            return httpx.Response(
                200,
                json={
                    "id": "4567890123",
                    "status": "current",
                    "title": "Confluence Rollback SOP",
                    "spaceId": "111222333",
                    "version": {"number": 4},
                    "body": {
                        "storage": {
                            "value": "<p>Every Confluence SOP requires a rollback plan.</p>",
                            "representation": "storage",
                        }
                    },
                },
            )
        if request.url.path == "/wiki/api/v2/spaces":
            if "cursor" not in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "results": [{"id": 111222333, "key": "OPS", "name": "Ops"}],
                        "_links": {"next": f"https://{SITE_HOST}/wiki/api/v2/spaces?cursor=page-2"},
                    },
                )
            return httpx.Response(
                200,
                json={"results": [{"id": "444555666", "key": "LEGAL", "name": "Legal"}]},
            )
        return httpx.Response(404, json={"message": "missing"})

    client = ConfluenceClient(
        email="bot@example.com",
        api_token="token-test",
        site_host=SITE_HOST,
        transport=httpx.MockTransport(responder),
    )
    try:
        fetched = await client.fetch_page(page_id="4567890123")
        spaces = await client.list_spaces()
    finally:
        await client.aclose()
    assert fetched.page_id == "4567890123"
    assert "rollback plan" in fetched.content
    assert [space.space_id for space in spaces] == ["111222333", "444555666"]
    assert any(path.endswith("/wiki/api/v2/pages/4567890123") for path in seen)


@pytest.mark.asyncio
async def test_confluence_client_rejects_off_origin_pagination() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "results": [{"id": "111222333", "key": "OPS", "name": "Ops"}],
                "_links": {"next": "https://evil.example/wiki/api/v2/spaces?cursor=x"},
            },
        )

    client = ConfluenceClient(
        email="bot@example.com",
        api_token="token-test",
        site_host=SITE_HOST,
        transport=httpx.MockTransport(responder),
    )
    try:
        with pytest.raises(ConfluenceResponseError) as caught:
            await client.list_spaces()
        assert caught.value.code == "confluence_response_invalid"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_confluence_executor_ingests_through_knowledge_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_CONFLUENCE_EMAIL", "bot@example.com")
    document = Document(
        id=uuid4(),
        organization_id=uuid4(),
        source="confluence",
        external_id="4567890123",
        title="Confluence Rollback SOP",
        classification=Classification.INTERNAL,
        acl={"organization": True},
        current_version=1,
    )
    version = DocumentVersion(
        id=uuid4(),
        organization_id=document.organization_id,
        document_id=document.id,
        version=1,
        media_type="text/html",
        extracted_text="body",
        checksum_sha256="abc",
        parser_version="html-bs4-v1",
        metadata_json={},
    )

    class _Service:
        async def ingest(
            self, *args: object, **kwargs: object
        ) -> tuple[Document, DocumentVersion, int]:
            del args, kwargs
            return document, version, 2

    async def fake_fetch(**kwargs: object) -> ConfluencePage:
        del kwargs
        return _page()

    monkeypatch.setattr(
        "obsion.knowledge.confluence.fetch_authorized_confluence_page",
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
            "operation": "knowledge.ingest",
            "page_id": "4567890123",
            "acl": {"organization": True},
        },
        "token-test",
        ConnectorContext(
            principal=Principal(
                id=uuid4(),
                organization_id=connector.organization_id,
                external_id="phase66-user",
                display_name="Phase 66",
                permissions=frozenset({"knowledge.write"}),
            ),
            run_id=uuid4(),
            step_id=None,
            session=object(),  # type: ignore[arg-type]
        ),
    )
    assert result.data["source"] == "confluence"
    assert result.data["operation"] == "knowledge.ingest"
    assert result.data["external_id"] == "4567890123"


def test_confluence_page_ingest_enters_knowledge_pipeline_and_harness_citations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_CONFLUENCE_EMAIL", "bot@example.com")
    monkeypatch.setenv("OBSION_CONFLUENCE_API_TOKEN", "token-test")

    async def fake_fetch(**kwargs: object) -> ConfluencePage:
        del kwargs
        return _page()

    monkeypatch.setattr(
        "obsion.knowledge.confluence.fetch_authorized_confluence_page",
        fake_fetch,
    )
    ingested = client.post(
        "/api/v1/knowledge/sources/confluence/pages",
        json={"page_id": "4567890123", "acl": {"organization": True}},
    )
    assert ingested.status_code == 201, ingested.text
    payload = ingested.json()
    assert payload["source"] == "confluence"
    assert payload["external_id"] == "4567890123"
    assert payload["chunk_count"] >= 1

    search = client.post(
        "/api/v1/knowledge/search",
        json={"query": "confluence rollback plan", "limit": 8},
    )
    assert search.status_code == 200, search.text
    assert search.json()[0]["source"] == "confluence"

    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Confluence Knowledge", "description": "Phase 66"},
    )
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Confluence QA"},
    )
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "What does the Confluence rollback SOP require?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    assert run["intent"]["route"] == "KNOWLEDGE"
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    assert artifacts[0]["inline_content"]["citations"]


def test_confluence_space_sync_skips_non_current_pages(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_CONFLUENCE_EMAIL", "bot@example.com")
    monkeypatch.setenv("OBSION_CONFLUENCE_API_TOKEN", "token-test")

    async def fake_pages(*args: object, **kwargs: object) -> list[ConfluenceSpacePage]:
        del args, kwargs
        return [
            ConfluenceSpacePage(
                space_id="111222333",
                page_id="4567890123",
                title="Confluence Rollback SOP",
                status="current",
            ),
            ConfluenceSpacePage(
                space_id="111222333",
                page_id="999888777",
                title="Draft",
                status="draft",
            ),
        ]

    async def fake_fetch(**kwargs: object) -> ConfluencePage:
        del kwargs
        return _page()

    monkeypatch.setattr("obsion.knowledge.confluence.list_confluence_pages", fake_pages)
    monkeypatch.setattr(
        "obsion.knowledge.confluence.fetch_authorized_confluence_page",
        fake_fetch,
    )
    synced = client.post(
        "/api/v1/knowledge/sources/confluence/spaces/111222333/sync",
        json={"acl": {"organization": True}},
    )
    assert synced.status_code == 201, synced.text
    payload = synced.json()
    assert payload["ingested_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["skipped"][0]["reason"] == "confluence_page_id_invalid"


def test_confluence_ingest_requires_acl_and_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSION_CONFLUENCE_API_TOKEN", raising=False)
    missing = client.post(
        "/api/v1/knowledge/sources/confluence/pages",
        json={"page_id": "4567890123", "acl": {"organization": True}},
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == "credential_unavailable"

    monkeypatch.setenv("OBSION_CONFLUENCE_EMAIL", "bot@example.com")
    monkeypatch.setenv("OBSION_CONFLUENCE_API_TOKEN", "token-test")

    async def fake_fetch(**kwargs: object) -> ConfluencePage:
        del kwargs
        return _page()

    monkeypatch.setattr(
        "obsion.knowledge.confluence.fetch_authorized_confluence_page",
        fake_fetch,
    )
    denied = client.post(
        "/api/v1/knowledge/sources/confluence/pages",
        json={"page_id": "4567890123"},
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "document_acl_required"


def test_confluence_knowledge_is_capability_not_experience() -> None:
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    knowledge = (WEB_ROOT / "src" / "components" / "knowledge-view.tsx").read_text(encoding="utf-8")
    assert "confluence" in admin
    assert "摄取 Confluence" in knowledge
    source = ast.parse((SOURCE_ROOT / "capabilities" / "confluence.py").read_text(encoding="utf-8"))
    imports = [
        node.module
        for node in ast.walk(source)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.harness.runtime" not in imports
    assert "obsion.capabilities.feishu_docs" not in imports
    assert "obsion_im.feishu" not in imports


@pytest.mark.skipif(
    os.environ.get("OBSION_CONFLUENCE_LIVE") != "1",
    reason="Live Confluence fetch is operator-owned",
)
@pytest.mark.asyncio
async def test_confluence_live_missing_page_fails_closed() -> None:
    email = os.environ.get("OBSION_CONFLUENCE_EMAIL", "")
    token = os.environ.get("OBSION_CONFLUENCE_API_TOKEN", "")
    site_host = os.environ.get("OBSION_CONFLUENCE_SITE_HOST", "")
    if not email or not token or not site_host:
        pytest.skip("Live Confluence credentials are not available")
    client = ConfluenceClient(email=email, api_token=token, site_host=site_host)
    try:
        health = await client.health()
        assert health["authenticated"] is True
        with pytest.raises((ConfluenceDeniedError, ValidationError)):
            await client.fetch_page(page_id="1")
    finally:
        await client.aclose()
