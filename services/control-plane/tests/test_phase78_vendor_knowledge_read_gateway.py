from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.api.dependencies import get_capability_gateway
from obsion.capabilities.confluence import ConfluenceSpace, ConfluenceSpacePage
from obsion.capabilities.dingtalk_docs import DingTalkWorkspace, DingTalkWorkspaceNode
from obsion.capabilities.feishu_docs import FeishuWikiNode, FeishuWikiSpace
from obsion.capabilities.wecom_docs import WeComSpace, WeComSpaceNode
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion"


def _set_vendor_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase78-feishu-secret")
    monkeypatch.setenv("OBSION_DINGTALK_APP_SECRET", "phase78-dingtalk-secret")
    monkeypatch.setenv("OBSION_WECOM_CORP_SECRET", "phase78-wecom-secret")
    monkeypatch.setenv("OBSION_CONFLUENCE_API_TOKEN", "phase78-confluence-secret")


def _install_vendor_browse_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def feishu_spaces(*args: object, **kwargs: object) -> list[FeishuWikiSpace]:
        del args, kwargs
        return [FeishuWikiSpace("7365887123", "飞书空间", "Feishu description")]

    async def feishu_nodes(*args: object, **kwargs: object) -> list[FeishuWikiNode]:
        del args, kwargs
        return [
            FeishuWikiNode(
                space_id="7365887123",
                node_token="wikcnPhase78Node",
                obj_token="doxcnPhase78Document",
                obj_type="docx",
                title="飞书文档",
            )
        ]

    async def dingtalk_workspaces(*args: object, **kwargs: object) -> list[DingTalkWorkspace]:
        del args, kwargs
        return [DingTalkWorkspace("workspace-phase78", "钉钉空间", "DingTalk description")]

    async def dingtalk_nodes(*args: object, **kwargs: object) -> list[DingTalkWorkspaceNode]:
        del args, kwargs
        return [
            DingTalkWorkspaceNode(
                workspace_id="workspace-phase78",
                node_id="node-phase78",
                document_id="document-phase78",
                node_type="document",
                title="钉钉文档",
            )
        ]

    async def wecom_space(*args: object, **kwargs: object) -> WeComSpace:
        del args, kwargs
        return WeComSpace("space_phase78", "企微空间", "WeCom description")

    async def wecom_nodes(*args: object, **kwargs: object) -> list[WeComSpaceNode]:
        del args, kwargs
        return [
            WeComSpaceNode(
                space_id="space_phase78",
                node_id="file_phase78",
                document_id="document_phase78",
                node_type="document",
                title="企微文档",
            )
        ]

    async def confluence_spaces(*args: object, **kwargs: object) -> list[ConfluenceSpace]:
        del args, kwargs
        return [ConfluenceSpace("78001", "PHASE78", "Confluence Space")]

    async def confluence_pages(*args: object, **kwargs: object) -> list[ConfluenceSpacePage]:
        del args, kwargs
        return [ConfluenceSpacePage("78001", "78002", "Confluence Page", "current")]

    monkeypatch.setattr("obsion.knowledge.feishu.list_feishu_spaces", feishu_spaces)
    monkeypatch.setattr("obsion.knowledge.feishu.list_feishu_nodes", feishu_nodes)
    monkeypatch.setattr("obsion.knowledge.dingtalk.list_dingtalk_workspaces", dingtalk_workspaces)
    monkeypatch.setattr(
        "obsion.knowledge.dingtalk.list_dingtalk_workspace_nodes",
        dingtalk_nodes,
    )
    monkeypatch.setattr("obsion.knowledge.wecom.describe_wecom_space", wecom_space)
    monkeypatch.setattr("obsion.knowledge.wecom.list_wecom_space_nodes", wecom_nodes)
    monkeypatch.setattr("obsion.knowledge.confluence.list_confluence_spaces", confluence_spaces)
    monkeypatch.setattr("obsion.knowledge.confluence.list_confluence_pages", confluence_pages)


def test_all_vendor_browse_routes_use_policy_audit_and_preserve_responses(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_vendor_credentials(monkeypatch)
    _install_vendor_browse_fakes(monkeypatch)
    cases = (
        ("/api/v1/knowledge/sources/feishu/spaces", "7365887123"),
        ("/api/v1/knowledge/sources/feishu/spaces/7365887123/nodes", "wikcnPhase78Node"),
        ("/api/v1/knowledge/sources/dingtalk/workspaces", "workspace-phase78"),
        (
            "/api/v1/knowledge/sources/dingtalk/workspaces/workspace-phase78/nodes",
            "node-phase78",
        ),
        ("/api/v1/knowledge/sources/wecom/spaces/space_phase78", "space_phase78"),
        (
            "/api/v1/knowledge/sources/wecom/spaces/space_phase78/nodes",
            "file_phase78",
        ),
        ("/api/v1/knowledge/sources/confluence/spaces", "78001"),
        ("/api/v1/knowledge/sources/confluence/spaces/78001/pages", "78002"),
    )
    request_ids: set[str] = set()
    for path, expected in cases:
        request_id = uuid4()
        response = client.get(path, headers={"X-Request-ID": str(request_id)})
        assert response.status_code == 200, response.text
        assert expected in response.text
        request_ids.add(str(request_id))

    audit = client.get("/api/v1/admin/audit?limit=100")
    assert audit.status_code == 200, audit.text
    records = [item for item in audit.json() if item["correlation_id"] in request_ids]
    assert len(records) == len(cases)
    assert {item["outcome"] for item in records} == {"SUCCESS"}
    assert {item["actor_type"] for item in records} == {"USER"}
    assert {item["action"] for item in records} == {"knowledge.write"}
    assert all(item["policy_decision_id"] is not None for item in records)
    assert all(item["metadata"]["invocation_mode"] == "operator" for item in records)
    assert all("evidence_id" not in item["metadata"] for item in records)


def test_vendor_browse_policy_precedes_secret_resolution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    restricted = Principal(
        id=uuid4(),
        organization_id=client.app.state.settings.dev_organization_id,
        external_id="phase78-restricted",
        display_name="Phase 78 Restricted",
        roles=frozenset({"viewer"}),
        permissions=frozenset({"knowledge.read"}),
    )
    request_id = uuid4()
    client.app.dependency_overrides[get_principal] = lambda: restricted
    try:
        response = client.get(
            "/api/v1/knowledge/sources/feishu/spaces",
            headers={"X-Request-ID": str(request_id)},
        )
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "capability_denied"

    audit = client.get("/api/v1/admin/audit?limit=100").json()
    record = next(item for item in audit if item["correlation_id"] == str(request_id))
    assert record["outcome"] == "DENIED"
    assert record["metadata"]["capability"] == "knowledge.source.containers"


def test_vendor_browse_rate_limit_precedes_secret_resolution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DenyRateLimiter:
        async def allow(self, key: str, limit: int | None = None) -> bool:
            del key, limit
            return False

    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    override = client.app.dependency_overrides.get(get_capability_gateway)
    gateway = override() if override is not None else client.app.state.capability_gateway
    original = gateway.rate_limiter
    gateway.rate_limiter = DenyRateLimiter()
    try:
        response = client.get("/api/v1/knowledge/sources/feishu/spaces")
    finally:
        gateway.rate_limiter = original
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "capability_rate_limited"


def test_vendor_browse_capabilities_are_versioned_l1_read_only(client: TestClient) -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200, response.text
    descriptors = {item["name"]: item for item in response.json()}
    for name in ("knowledge.source.containers", "knowledge.source.items"):
        descriptor = descriptors[name]
        assert descriptor["version"] >= 1
        assert descriptor["transport"] == "HTTP"
        assert descriptor["risk"] == "L1"
        assert descriptor["side_effect"] == "NONE"
        assert descriptor["permission"] == "knowledge.write"


def test_all_vendor_get_routes_use_no_run_capability_gateway() -> None:
    source = (SOURCE_ROOT / "api" / "knowledge.py").read_text(encoding="utf-8")
    route_functions = (
        "list_feishu_source_spaces",
        "list_feishu_source_nodes",
        "list_dingtalk_source_workspaces",
        "list_dingtalk_source_nodes",
        "get_wecom_source_space",
        "list_wecom_source_nodes",
        "list_confluence_source_spaces",
        "list_confluence_source_pages",
    )
    for name in route_functions:
        start = source.index(f"async def {name}")
        end = source.find("\n@router.", start)
        body = source[start : end if end >= 0 else len(source)]
        assert "_invoke_vendor_browse(" in body

    assert "CredentialBroker" not in source
    assert "resolve_feishu_docs_connector" not in source
    assert "resolve_dingtalk_docs_connector" not in source
    assert "resolve_wecom_docs_connector" not in source
    assert "resolve_confluence_connector" not in source

    gateway = (SOURCE_ROOT / "capabilities" / "gateway.py").read_text(encoding="utf-8")
    operator = gateway.split("async def _invoke_operator", 1)[1].split("async def _invoke", 1)[0]
    assert "VENDOR_KNOWLEDGE_BROWSE_OPERATIONS" in operator
    assert "SideEffect.NONE" in operator
    assert "run_id=None" in operator
    assert "self.events.append" not in operator
    assert "self._evidence" not in operator


@pytest.mark.skipif(
    os.environ.get("OBSION_FEISHU_BROWSE_LIVE") != "1",
    reason="Live Feishu Capability browse is operator-owned",
)
@pytest.mark.feishu_browse_live
def test_feishu_live_browse_traverses_operator_gateway(client: TestClient) -> None:
    request_id = uuid4()
    response = client.get(
        "/api/v1/knowledge/sources/feishu/spaces",
        headers={"X-Request-ID": str(request_id)},
    )
    assert response.status_code in {200, 403}, response.text
    if response.status_code == 403:
        assert response.json()["code"] == "feishu_docs_upstream_denied"

    audit = client.get("/api/v1/admin/audit?limit=100")
    assert audit.status_code == 200, audit.text
    record = next(item for item in audit.json() if item["correlation_id"] == str(request_id))
    assert record["policy_decision_id"] is not None
    assert record["metadata"]["invocation_mode"] == "operator"
    assert record["metadata"]["capability"] == "knowledge.source.containers"
    assert "evidence_id" not in record["metadata"]
