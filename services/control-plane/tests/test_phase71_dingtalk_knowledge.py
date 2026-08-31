from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.capabilities.dingtalk_docs import (
    DINGTALK_ORIGIN,
    DingTalkDocsClient,
    DingTalkDocument,
    assert_dingtalk_docs_egress,
    merge_permission_members,
    normalize_document_id,
)
from obsion.common.errors import ValidationError
from obsion.db.models import Connector
from obsion.domain.enums import Classification, ConnectorStatus
from obsion.knowledge.dingtalk import resolve_ingest_acl
from obsion.security.identity import Principal

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion"


def _document() -> DingTalkDocument:
    return DingTalkDocument(
        document_id="docPhase71Rollback",
        title="Rollback SOP",
        content="# Rollback SOP\nEvery production release requires an owner and rollback plan.",
        revision_id="3",
        workspace_id="ws_ops",
    )


def _connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="obsion-dingtalk-docs",
        connector_type="dingtalk-docs",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint=DINGTALK_ORIGIN,
        configuration={"protocol": "dingtalk.docs.v1", "app_key_env": "OBSION_DINGTALK_APP_KEY"},
        credential_ref="env://OBSION_DINGTALK_APP_SECRET",
        declared_grants=["knowledge.write"],
        allowed_egress=["https://api.dingtalk.com"],
    )


async def _fake_fetch(**kwargs: object) -> DingTalkDocument:
    del kwargs
    return _document()


def test_dingtalk_document_id_and_acl_fail_closed() -> None:
    with pytest.raises(ValidationError) as caught:
        normalize_document_id("short")
    assert caught.value.code == "dingtalk_docs_document_id_invalid"
    with pytest.raises(ValidationError) as caught:
        resolve_ingest_acl(requested=None, inherited=None, inherit_acl=False)
    assert caught.value.code == "document_acl_required"


def test_dingtalk_permission_members_never_invent_organization_acl() -> None:
    assert merge_permission_members({"members": []}) is None
    mapped = merge_permission_members(
        {
            "members": [
                {"memberType": "userid", "memberId": "staff_owner"},
                {"memberType": "dept", "memberId": "dept_eng"},
            ]
        }
    )
    assert mapped is not None
    assert mapped["organization"] is False
    assert mapped["users"] == ["staff_owner"]
    assert mapped["departments"] == ["dept_eng"]


def test_dingtalk_docs_egress_is_pinned_to_official_origin() -> None:
    connector = _connector()
    assert_dingtalk_docs_egress(connector)
    connector.endpoint = "https://evil.example"
    with pytest.raises(ValidationError) as caught:
        assert_dingtalk_docs_egress(connector)
    assert caught.value.code == "connector_egress_denied"
    connector.endpoint = DINGTALK_ORIGIN
    connector.configuration = {"protocol": "dingtalk.docs.v1", "base_url": "https://evil.example"}
    with pytest.raises(ValidationError) as caught:
        assert_dingtalk_docs_egress(connector)
    assert caught.value.code == "dingtalk_docs_operation_invalid"


@pytest.mark.asyncio
async def test_dingtalk_docs_client_fetches_document_content() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "t-test", "expireIn": 7200})
        if request.url.path.endswith("/documents/docPhase71Rollback"):
            return httpx.Response(
                200,
                json={"name": "Rollback SOP", "version": "3", "workspaceId": "ws_ops"},
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(200, json={"content": "body text"})
        return httpx.Response(404, json={"code": "NotFound"})

    client = DingTalkDocsClient(
        app_key="ding-key",
        app_secret="ding-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        fetched = await client.fetch_document(document_id="docPhase71Rollback")
    finally:
        await client.aclose()
    assert fetched.title == "Rollback SOP"
    assert fetched.content == "body text"
    assert fetched.workspace_id == "ws_ops"


@pytest.mark.asyncio
async def test_http_executor_ingests_dingtalk_through_knowledge_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from obsion.config import Environment, Settings
    from obsion.db.models import Document, DocumentVersion

    monkeypatch.setenv("OBSION_DINGTALK_APP_KEY", "ding-key")
    document = Document(
        id=uuid4(),
        organization_id=uuid4(),
        source="dingtalk",
        external_id="docPhase71Rollback",
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

    monkeypatch.setattr(
        "obsion.knowledge.dingtalk.fetch_authorized_dingtalk_document",
        _fake_fetch,
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
            "document_id": "docPhase71Rollback",
            "acl": {"organization": True},
        },
        "ding-secret",
        ConnectorContext(
            principal=Principal(
                id=uuid4(),
                organization_id=connector.organization_id,
                external_id="phase71-user",
                display_name="Phase 71",
                permissions=frozenset({"knowledge.write"}),
            ),
            run_id=uuid4(),
            step_id=None,
            session=object(),  # type: ignore[arg-type]
        ),
    )
    assert result.data["source"] == "dingtalk"
    assert result.data["chunk_count"] == 2
    assert result.data["operation"] == "knowledge.ingest"


def test_dingtalk_docs_is_capability_not_experience() -> None:
    module = (SOURCE_ROOT / "capabilities" / "dingtalk_docs.py").read_text(encoding="utf-8")
    tree = ast.parse(module)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.harness.runtime" not in imports
    assert "obsion_im" not in "\n".join(imports)
    assert "api.dingtalk.com" in module
    knowledge = (WEB_ROOT / "src" / "components" / "knowledge-view.tsx").read_text(encoding="utf-8")
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "dingtalk" in knowledge.lower() or "钉钉" in knowledge
    assert "dingtalk-docs" in admin or "钉钉" in admin


def test_rest_ingests_dingtalk_document(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OBSION_DINGTALK_APP_KEY", "ding-key")
    monkeypatch.setenv("OBSION_DINGTALK_APP_SECRET", "ding-secret")
    monkeypatch.setattr(
        "obsion.knowledge.dingtalk.fetch_authorized_dingtalk_document",
        _fake_fetch,
    )
    response = client.post(
        "/api/v1/knowledge/sources/dingtalk/documents",
        json={
            "document_id": "docPhase71Rollback",
            "acl": {"organization": True},
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["source"] == "dingtalk"
    assert payload["external_id"] == "docPhase71Rollback"
    search = client.post(
        "/api/v1/knowledge/search",
        json={"query": "rollback", "limit": 5},
    )
    assert search.status_code == 200, search.text
    assert search.json()[0]["source"] == "dingtalk"


def test_rest_dingtalk_requires_acl(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OBSION_DINGTALK_APP_KEY", "ding-key")
    monkeypatch.setenv("OBSION_DINGTALK_APP_SECRET", "ding-secret")
    monkeypatch.setattr(
        "obsion.knowledge.dingtalk.fetch_authorized_dingtalk_document",
        _fake_fetch,
    )
    response = client.post(
        "/api/v1/knowledge/sources/dingtalk/documents",
        json={"document_id": "docPhase71Rollback"},
    )
    assert response.status_code == 422, response.text
