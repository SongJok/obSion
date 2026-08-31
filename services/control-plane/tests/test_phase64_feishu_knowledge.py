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
    assert_feishu_docs_egress,
    merge_permission_members,
    normalize_document_id,
)
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector, Document, DocumentVersion
from obsion.domain.enums import Classification, ConnectorStatus
from obsion.knowledge.feishu import resolve_ingest_acl
from obsion.release.live_evidence import write_probe_record
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
        document_id="doxcnPhase64Rollback",
        title="Rollback SOP",
        content="# Rollback SOP\nEvery production release requires an owner and rollback plan.",
        revision_id="12",
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


async def _fake_fetch(**kwargs: object) -> FeishuDocument:
    del kwargs
    return _feishu_document()


def test_feishu_document_id_and_acl_fail_closed() -> None:
    with pytest.raises(ValidationError) as caught:
        normalize_document_id("short")
    assert caught.value.code == "feishu_docs_document_id_invalid"
    with pytest.raises(ValidationError) as caught:
        resolve_ingest_acl(requested=None, inherited=None, inherit_acl=False)
    assert caught.value.code == "document_acl_required"
    with pytest.raises(ValidationError) as caught:
        resolve_ingest_acl(requested=None, inherited=None, inherit_acl=True)
    assert caught.value.code == "document_acl_required"
    acl = resolve_ingest_acl(
        requested={"organization": True},
        inherited={"users": ["ou_1"], "organization": False},
        inherit_acl=True,
    )
    assert acl["organization"] is True
    assert "ou_1" in acl["users"]


def test_feishu_permission_members_never_invent_organization_acl() -> None:
    assert merge_permission_members({"data": {"items": []}}) is None
    mapped = merge_permission_members(
        {
            "data": {
                "items": [
                    {"member_type": "userid", "member_id": "ou_owner"},
                    {"member_type": "opendepartmentid", "member_id": "od_eng"},
                ]
            }
        }
    )
    assert mapped is not None
    assert mapped["organization"] is False
    assert mapped["users"] == ["ou_owner"]
    assert mapped["departments"] == ["od_eng"]


def test_feishu_docs_egress_is_pinned_to_official_origin() -> None:
    connector = _connector()
    assert_feishu_docs_egress(connector)
    connector.endpoint = "https://evil.example"
    with pytest.raises(ValidationError) as caught:
        assert_feishu_docs_egress(connector)
    assert caught.value.code == "connector_egress_denied"
    connector.endpoint = FEISHU_ORIGIN
    connector.configuration = {"protocol": "feishu.docs.v1", "base_url": "https://evil.example"}
    with pytest.raises(ValidationError) as caught:
        assert_feishu_docs_egress(connector)
    assert caught.value.code == "feishu_docs_operation_invalid"


@pytest.mark.asyncio
async def test_feishu_docs_client_fetches_docx_and_resolves_wiki_nodes() -> None:
    seen: list[str] = []

    async def responder(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/tenant_access_token/internal/"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-test", "expire": 3600}
            )
        if request.url.path == "/open-apis/wiki/v2/spaces/get_node":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "node": {
                            "obj_type": "docx",
                            "obj_token": "doxcnFromWiki",
                            "title": "Wiki SOP",
                        }
                    },
                },
            )
        if request.url.path.endswith("/documents/doxcnFromWiki"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"document": {"title": "Wiki SOP", "revision_id": 3}},
                },
            )
        if request.url.path.endswith("/raw_content"):
            return httpx.Response(200, json={"code": 0, "data": {"content": "Wiki body"}})
        return httpx.Response(404, json={"code": 1, "msg": "missing"})

    client = FeishuDocsClient(
        app_id="cli_test",
        app_secret="secret_test",
        transport=httpx.MockTransport(responder),
    )
    try:
        fetched = await client.fetch_document(document_id="wikcnPhase64Node", obj_type="wiki")
    finally:
        await client.aclose()
    assert fetched.document_id == "doxcnFromWiki"
    assert fetched.title == "Wiki SOP"
    assert fetched.content == "Wiki body"
    assert fetched.wiki_token == "wikcnPhase64Node"
    assert any(path.endswith("/tenant_access_token/internal/") for path in seen)


@pytest.mark.asyncio
async def test_feishu_docs_client_fails_closed_for_sheet_wiki_nodes() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal/"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-test", "expire": 3600}
            )
        return httpx.Response(
            200,
            json={"code": 0, "data": {"node": {"obj_type": "sheet", "obj_token": "shtcn1"}}},
        )

    client = FeishuDocsClient(
        app_id="cli_test",
        app_secret="secret_test",
        transport=httpx.MockTransport(responder),
    )
    try:
        with pytest.raises(ValidationError) as caught:
            await client.fetch_document(document_id="wikcnSheetNode", obj_type="wiki")
        assert caught.value.code == "feishu_docs_obj_type_unsupported"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_feishu_docs_client_maps_missing_scope_to_denied() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal/"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-test", "expire": 3600}
            )
        return httpx.Response(200, json={"code": 99991663, "msg": "no permission"})

    client = FeishuDocsClient(
        app_id="cli_test",
        app_secret="secret_test",
        transport=httpx.MockTransport(responder),
    )
    try:
        with pytest.raises(FeishuDocsDeniedError):
            await client.fetch_document(document_id="doxcnMissingScope", obj_type="docx")
    finally:
        await client.aclose()


@pytest.mark.parametrize("vendor_code", [99991672, 99992402])
@pytest.mark.asyncio
async def test_feishu_docs_client_classifies_http_400_denials(vendor_code: int) -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal/"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-test", "expire": 3600}
            )
        return httpx.Response(
            400,
            json={
                "code": vendor_code,
                "msg": "cli_test secret_test t-test resource is inaccessible",
            },
        )

    client = FeishuDocsClient(
        app_id="cli_test",
        app_secret="secret_test",
        transport=httpx.MockTransport(responder),
    )
    try:
        with pytest.raises(FeishuDocsDeniedError) as denied:
            await client.fetch_document(document_id="doxcnMissingResource", obj_type="docx")
    finally:
        await client.aclose()
    rendered = str(denied.value)
    assert str(vendor_code) in rendered
    assert "cli_test" not in rendered
    assert "secret_test" not in rendered
    assert "t-test" not in rendered


@pytest.mark.asyncio
async def test_feishu_docs_executor_ingests_through_knowledge_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "cli_test")
    document = Document(
        id=uuid4(),
        organization_id=uuid4(),
        source="feishu",
        external_id="doxcnPhase64Rollback",
        title="Rollback SOP",
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

    async def fake_fetch(**kwargs: object) -> FeishuDocument:
        del kwargs
        return _feishu_document()

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
            "operation": "knowledge.ingest",
            "document_id": "doxcnPhase64Rollback",
            "acl": {"organization": True},
        },
        "secret_test",
        ConnectorContext(
            principal=Principal(
                id=uuid4(),
                organization_id=connector.organization_id,
                external_id="phase64-user",
                display_name="Phase 64",
                permissions=frozenset({"knowledge.write"}),
            ),
            run_id=uuid4(),
            step_id=None,
            session=object(),  # type: ignore[arg-type]
        ),
    )
    assert result.data["source"] == "feishu"
    assert result.data["chunk_count"] == 2
    assert result.data["operation"] == "knowledge.ingest"


def test_feishu_document_ingest_enters_knowledge_pipeline_and_harness_citations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "secret_test")
    monkeypatch.setattr(
        "obsion.knowledge.feishu.fetch_authorized_feishu_document",
        _fake_fetch,
    )
    ingested = client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        json={
            "document_id": "doxcnPhase64Rollback",
            "acl": {"organization": True},
        },
    )
    assert ingested.status_code == 201, ingested.text
    payload = ingested.json()
    assert payload["source"] == "feishu"
    assert payload["external_id"] == "doxcnPhase64Rollback"
    assert payload["obj_type"] == "docx"
    assert payload["chunk_count"] >= 1

    search = client.post(
        "/api/v1/knowledge/search",
        json={"query": "rollback plan", "limit": 8},
    )
    assert search.status_code == 200, search.text
    assert search.json()[0]["source"] == "feishu"

    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Feishu Knowledge", "description": "Phase 64"},
    )
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Feishu QA"},
    )
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "What does the rollback SOP require?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    assert run["intent"]["route"] == "KNOWLEDGE"
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    assert artifacts[0]["inline_content"]["citations"]


def test_feishu_document_ingest_requires_acl_and_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    missing = client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        json={"document_id": "doxcnPhase64Rollback", "acl": {"organization": True}},
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["code"] == "credential_unavailable"

    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "secret_test")
    monkeypatch.setattr(
        "obsion.knowledge.feishu.fetch_authorized_feishu_document",
        _fake_fetch,
    )
    denied = client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        json={"document_id": "doxcnPhase64Rollback"},
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == "document_acl_required"


def test_feishu_knowledge_is_capability_not_experience() -> None:
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    knowledge = (WEB_ROOT / "src" / "components" / "knowledge-view.tsx").read_text(encoding="utf-8")
    assert "feishu-docs" in admin
    assert "飞书文档" in knowledge
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
    reason="Live Feishu document fetch is operator-owned",
)
@pytest.mark.asyncio
@pytest.mark.live
async def test_feishu_docs_live_missing_document_fails_closed() -> None:
    app_id = os.environ.get("OBSION_FEISHU_APP_ID", "")
    app_secret = os.environ.get("OBSION_FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        pytest.skip("Live Feishu credentials are not available")
    client = FeishuDocsClient(app_id=app_id, app_secret=app_secret)
    try:
        health = await client.health()
        assert health["authenticated"] is True
        with pytest.raises((FeishuDocsDeniedError, ValidationError)) as exc_info:
            await client.fetch_document(
                document_id="doxcnPhase64DoesNotExistToken",
                obj_type="docx",
            )
    finally:
        await client.aclose()
    write_probe_record("feishu-docs-missing-denial", "denied", type(exc_info.value).__name__)
