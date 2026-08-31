from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from obsion.api.dependencies import get_capability_gateway
from obsion.capabilities.feishu_docs import FeishuDocument
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion"


async def _fake_feishu_fetch(**kwargs: object) -> FeishuDocument:
    del kwargs
    return FeishuDocument(
        document_id="doxcnPhase77Gateway",
        title="Gateway SOP",
        content="Every vendor Knowledge write passes Policy and Audit.",
        revision_id="77",
        obj_type="docx",
        wiki_token=None,
    )


def _post_feishu(
    client: TestClient,
    *,
    request_id: UUID,
) -> Response:
    return client.post(
        "/api/v1/knowledge/sources/feishu/documents",
        headers={"X-Request-ID": str(request_id)},
        json={
            "document_id": "doxcnPhase77Gateway",
            "acl": {"organization": True},
        },
    )


def test_operator_rest_write_persists_policy_and_audit_without_fake_run(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSION_FEISHU_APP_ID", "phase77-app")
    monkeypatch.setenv("OBSION_FEISHU_APP_SECRET", "phase77-secret")
    monkeypatch.setattr(
        "obsion.knowledge.feishu.fetch_authorized_feishu_document",
        _fake_feishu_fetch,
    )
    request_id = uuid4()
    response = _post_feishu(client, request_id=request_id)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["source"] == "feishu"
    assert UUID(payload["version_id"])

    audit = client.get("/api/v1/admin/audit?limit=100")
    assert audit.status_code == 200, audit.text
    record = next(
        item
        for item in audit.json()
        if item["correlation_id"] == str(request_id) and item["action"] == "knowledge.write"
    )
    assert record["actor_type"] == "USER"
    assert record["outcome"] == "SUCCESS"
    assert record["policy_decision_id"] is not None
    assert record["metadata"]["invocation_mode"] == "operator"
    assert record["metadata"]["capability_version_id"] is not None
    assert "evidence_id" not in record["metadata"]


def test_operator_rest_policy_denial_commits_before_http_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    request_id = uuid4()
    restricted = Principal(
        id=uuid4(),
        organization_id=client.app.state.settings.dev_organization_id,
        external_id="phase77-restricted",
        display_name="Phase 77 Restricted",
        roles=frozenset({"viewer"}),
        permissions=frozenset({"knowledge.read"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: restricted
    try:
        response = _post_feishu(client, request_id=request_id)
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "capability_denied"

    audit = client.get("/api/v1/admin/audit?limit=100")
    record = next(item for item in audit.json() if item["correlation_id"] == str(request_id))
    assert record["outcome"] == "DENIED"
    assert record["policy_decision_id"] is not None
    assert record["metadata"]["invocation_mode"] == "operator"


def test_operator_rest_rate_limit_precedes_secret_resolution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DenyRateLimiter:
        async def allow(self, key: str, limit: int | None = None) -> bool:
            del key, limit
            return False

    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    gateway = client.app.dependency_overrides.get(get_capability_gateway)
    resolved_gateway = gateway() if gateway is not None else client.app.state.capability_gateway
    original = resolved_gateway.rate_limiter
    resolved_gateway.rate_limiter = _DenyRateLimiter()
    request_id = uuid4()
    try:
        response = _post_feishu(client, request_id=request_id)
    finally:
        resolved_gateway.rate_limiter = original
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "capability_rate_limited"


def test_operator_rest_ask_fails_closed_without_fabricating_approval(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = client.post(
        "/api/v1/admin/policies",
        json={
            "name": "phase77-operator-knowledge-ask",
            "priority": 9999,
            "effect": "ASK",
            "conditions": {
                "actions": ["knowledge.write"],
                "resource": {"source": "feishu"},
                "context": {"invocation_mode": "operator"},
            },
            "obligations": [],
            "reason": "Operator vendor writes require a Harness Run for approval",
        },
    )
    assert policy.status_code == 201, policy.text
    monkeypatch.delenv("OBSION_FEISHU_APP_SECRET", raising=False)
    request_id = uuid4()
    response = _post_feishu(client, request_id=request_id)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "capability_denied"

    approvals = client.get("/api/v1/approvals")
    assert approvals.status_code == 200, approvals.text
    assert approvals.json() == []
    audit = client.get("/api/v1/admin/audit?limit=100").json()
    record = next(item for item in audit if item["correlation_id"] == str(request_id))
    assert record["metadata"]["reason"] == "operator_capability_approval_requires_run"


def test_all_vendor_write_routes_use_operator_capability_gateway() -> None:
    source = (SOURCE_ROOT / "api" / "knowledge.py").read_text(encoding="utf-8")
    route_functions = (
        "ingest_feishu_source_document",
        "sync_feishu_source_space",
        "ingest_dingtalk_source_document",
        "sync_dingtalk_source_workspace",
        "ingest_wecom_source_document",
        "sync_wecom_source_space",
        "ingest_confluence_source_page",
        "sync_confluence_source_space",
    )
    for index, name in enumerate(route_functions):
        start = source.index(f"async def {name}")
        next_start = (
            source.index("\n@router.", start + 1)
            if index + 1 < len(route_functions)
            else len(source)
        )
        assert "_invoke_vendor_write(" in source[start:next_start]

    assert "ingest_feishu_document" not in source
    assert "ingest_dingtalk_document" not in source
    assert "ingest_wecom_document" not in source
    assert "ingest_confluence_page" not in source

    gateway = (SOURCE_ROOT / "capabilities" / "gateway.py").read_text(encoding="utf-8")
    operator = gateway.split("async def _invoke_operator", 1)[1].split("async def _invoke", 1)[0]
    assert "\n            PolicyInput(" not in operator
    assert "ResourcePolicyInput(" in operator
    assert "run_id=None" in operator
    assert "self.events.append" not in operator
    assert "self._evidence" not in operator
    assert 'request.capability_name in {"knowledge.ingest", "knowledge.sync"}' in operator
