from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.capabilities.wecom_docs import (
    WECOM_ORIGIN,
    WeComDocsClient,
    WeComDocument,
    assert_wecom_docs_egress,
    merge_permission_members,
    normalize_document_id,
)
from obsion.common.errors import ValidationError
from obsion.db.models import Connector
from obsion.domain.enums import Classification, ConnectorStatus
from obsion.knowledge.wecom import resolve_ingest_acl
from obsion.security.identity import Principal

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion"


def _document() -> WeComDocument:
    return WeComDocument(
        document_id="docPhase72Rollback",
        title="Rollback SOP",
        content="# Rollback SOP\nEvery production release requires an owner and rollback plan.",
        revision_id="3",
        space_id="sp_ops",
    )


def _connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="obsion-wecom-docs",
        connector_type="wecom-docs",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint=WECOM_ORIGIN,
        configuration={"protocol": "wecom.docs.v1", "corp_id_env": "OBSION_WECOM_CORP_ID"},
        credential_ref="env://OBSION_WECOM_CORP_SECRET",
        declared_grants=["knowledge.write"],
        allowed_egress=["https://qyapi.weixin.qq.com"],
    )


async def _fake_fetch(**kwargs: object) -> WeComDocument:
    del kwargs
    return _document()


def test_wecom_document_id_and_acl_fail_closed() -> None:
    with pytest.raises(ValidationError) as caught:
        normalize_document_id("short")
    assert caught.value.code == "wecom_docs_document_id_invalid"
    with pytest.raises(ValidationError) as caught:
        resolve_ingest_acl(requested=None, inherited=None, inherit_acl=False)
    assert caught.value.code == "document_acl_required"


def test_wecom_permission_members_never_invent_organization_acl() -> None:
    assert merge_permission_members({"doc_member_list": []}) is None
    mapped = merge_permission_members(
        {
            "doc_member_list": [
                {"type": 1, "userid": "user_owner"},
                {"type": 2, "departmentid": "1"},
            ]
        }
    )
    assert mapped is not None
    assert mapped["organization"] is False
    assert mapped["users"] == ["user_owner"]
    assert mapped["departments"] == ["1"]


def test_wecom_docs_egress_is_pinned_to_official_origin() -> None:
    connector = _connector()
    assert_wecom_docs_egress(connector)
    connector.endpoint = "https://evil.example"
    with pytest.raises(ValidationError) as caught:
        assert_wecom_docs_egress(connector)
    assert caught.value.code == "connector_egress_denied"
    connector.endpoint = WECOM_ORIGIN
    connector.configuration = {"protocol": "wecom.docs.v1", "base_url": "https://evil.example"}
    with pytest.raises(ValidationError) as caught:
        assert_wecom_docs_egress(connector)
    assert caught.value.code == "wecom_docs_operation_invalid"


@pytest.mark.asyncio
async def test_wecom_docs_client_fetches_document_content() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "t-test", "expires_in": 7200}
            )
        if request.url.path == "/cgi-bin/wedoc/get_doc_base_info":
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "doc_base_info": {
                        "docid": "docPhase72Rollback",
                        "doc_name": "Rollback SOP",
                        "version": 3,
                        "spaceid": "sp_ops",
                    },
                },
            )
        if request.url.path == "/cgi-bin/wedoc/document/get":
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "version": 3,
                    "document": {
                        "children": [
                            {"Text": {"content": "body text"}},
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"errcode": 404, "errmsg": "NotFound"})

    client = WeComDocsClient(
        corp_id="ww-corp",
        corp_secret="ww-secret",
        transport=httpx.MockTransport(responder),
    )
    try:
        fetched = await client.fetch_document(document_id="docPhase72Rollback")
    finally:
        await client.aclose()
    assert fetched.title == "Rollback SOP"
    assert fetched.content == "body text"
    assert fetched.space_id == "sp_ops"


@pytest.mark.asyncio
async def test_http_executor_ingests_wecom_through_knowledge_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from obsion.config import Environment, Settings
    from obsion.db.models import Document, DocumentVersion

    monkeypatch.setenv("OBSION_WECOM_CORP_ID", "ww-corp")
    document = Document(
        id=uuid4(),
        organization_id=uuid4(),
        source="wecom",
        external_id="docPhase72Rollback",
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
        "obsion.knowledge.wecom.fetch_authorized_wecom_document",
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
            "document_id": "docPhase72Rollback",
            "acl": {"organization": True},
        },
        "ww-secret",
        ConnectorContext(
            principal=Principal(
                id=uuid4(),
                organization_id=connector.organization_id,
                external_id="phase72-user",
                display_name="Phase 72",
                permissions=frozenset({"knowledge.write"}),
            ),
            run_id=uuid4(),
            step_id=None,
            session=object(),  # type: ignore[arg-type]
        ),
    )
    assert result.data["source"] == "wecom"
    assert result.data["chunk_count"] == 2
    assert result.data["operation"] == "knowledge.ingest"


def test_wecom_docs_is_capability_not_experience() -> None:
    module = (SOURCE_ROOT / "capabilities" / "wecom_docs.py").read_text(encoding="utf-8")
    tree = ast.parse(module)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.harness.runtime" not in imports
    assert "obsion_im" not in "\n".join(imports)
    assert "qyapi.weixin.qq.com" in module
    knowledge = (WEB_ROOT / "src" / "components" / "knowledge-view.tsx").read_text(encoding="utf-8")
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "wecom" in knowledge.lower() or "企微" in knowledge
    assert "wecom-docs" in admin or "企微" in admin


def test_rest_ingests_wecom_document(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OBSION_WECOM_CORP_ID", "ww-corp")
    monkeypatch.setenv("OBSION_WECOM_CORP_SECRET", "ww-secret")
    monkeypatch.setattr(
        "obsion.knowledge.wecom.fetch_authorized_wecom_document",
        _fake_fetch,
    )
    response = client.post(
        "/api/v1/knowledge/sources/wecom/documents",
        json={
            "document_id": "docPhase72Rollback",
            "acl": {"organization": True},
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["source"] == "wecom"
    assert payload["external_id"] == "docPhase72Rollback"
    search = client.post(
        "/api/v1/knowledge/search",
        json={"query": "rollback", "limit": 5},
    )
    assert search.status_code == 200, search.text
    assert search.json()[0]["source"] == "wecom"


def test_rest_wecom_requires_acl(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OBSION_WECOM_CORP_ID", "ww-corp")
    monkeypatch.setenv("OBSION_WECOM_CORP_SECRET", "ww-secret")
    monkeypatch.setattr(
        "obsion.knowledge.wecom.fetch_authorized_wecom_document",
        _fake_fetch,
    )
    response = client.post(
        "/api/v1/knowledge/sources/wecom/documents",
        json={"document_id": "docPhase72Rollback"},
    )
    assert response.status_code == 422, response.text
