from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.rate_limit import InMemoryFixedWindowRateLimiter
from obsion.common.errors import ValidationError
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus
from obsion.knowledge.connector_contract import (
    KnowledgeConnectorBudget,
    SyncBudgetTracker,
    VendorKnowledgeProvenance,
    attach_sync_result_envelope,
    enforce_knowledge_capability_rate_limit,
    provenance_fields_from_version,
)
from obsion.security.identity import Principal


def test_sync_budget_fail_closed_on_nodes() -> None:
    tracker = SyncBudgetTracker(KnowledgeConnectorBudget(max_pages=20, max_nodes=1, max_depth=8))
    tracker.consume_node()
    with pytest.raises(ValidationError) as caught:
        tracker.consume_node()
    assert caught.value.code == "knowledge_sync_budget_exceeded"
    assert caught.value.details["dimension"] == "nodes"


def test_sync_budget_invalid_config() -> None:
    connector = Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="obsion-dingtalk-docs",
        connector_type="dingtalk-docs",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint="https://api.dingtalk.com",
        configuration={"knowledge_sync_budget": {"max_nodes": 0}},
        credential_ref="env://OBSION_DINGTALK_APP_SECRET",
        declared_grants=["knowledge.write"],
        allowed_egress=["https://api.dingtalk.com"],
    )
    with pytest.raises(ValidationError) as caught:
        KnowledgeConnectorBudget.from_connector(connector)
    assert caught.value.code == "knowledge_sync_budget_invalid"


def test_sync_envelope_reports_budget_and_provenance() -> None:
    tracker = SyncBudgetTracker(KnowledgeConnectorBudget())
    tracker.consume_page()
    envelope = attach_sync_result_envelope(
        result={
            "operation": "knowledge.sync",
            "ingested": [],
            "skipped": [],
            "failed": [],
            "ingested_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        },
        budget=tracker.snapshot(),
        provenance=VendorKnowledgeProvenance(
            source="dingtalk",
            external_id="ws_ops",
            revision_id=None,
            connector_name="obsion-dingtalk-docs",
            connector_id=str(uuid4()),
            operation="knowledge.sync",
            sync_scope_id="ws_ops",
        ),
    )
    assert envelope["budget"]["exhausted"] is False
    assert envelope["budget"]["limits"]["max_nodes"] == 200
    assert envelope["provenance"]["connector_name"] == "obsion-dingtalk-docs"
    assert envelope["provenance"]["external_id"] == "ws_ops"


def test_provenance_fields_from_version_metadata() -> None:
    fields = provenance_fields_from_version(
        source="wecom",
        external_id="docPhase73",
        metadata={
            "revision_id": "9",
            "connector_name": "obsion-wecom-docs",
            "operation": "knowledge.ingest",
        },
    )
    assert fields == {
        "external_id": "docPhase73",
        "revision_id": "9",
        "connector_name": "obsion-wecom-docs",
        "operation": "knowledge.ingest",
    }


@pytest.mark.asyncio
async def test_rest_rate_limit_shares_gateway_semantics() -> None:
    limiter = InMemoryFixedWindowRateLimiter(1)
    principal = Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="phase73",
        display_name="Phase 73",
        permissions=frozenset({"knowledge.write"}),
    )
    connector = Connector(
        id=uuid4(),
        organization_id=principal.organization_id,
        name="obsion-wecom-docs",
        connector_type="wecom-docs",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint="https://qyapi.weixin.qq.com",
        configuration={"rate_limit_per_minute": 1, "protocol": "wecom.docs.v1"},
        credential_ref="env://OBSION_WECOM_CORP_SECRET",
        declared_grants=["knowledge.write"],
        allowed_egress=["https://qyapi.weixin.qq.com"],
    )

    class _Session:
        async def scalar(self, *_args: object, **_kwargs: object) -> None:
            return None

    session = _Session()
    await enforce_knowledge_capability_rate_limit(
        session,  # type: ignore[arg-type]
        rate_limiter=limiter,
        principal=principal,
        connector=connector,
        capability_name="knowledge.ingest",
        default_limit=120,
    )
    with pytest.raises(Exception) as caught:
        await enforce_knowledge_capability_rate_limit(
            session,  # type: ignore[arg-type]
            rate_limiter=limiter,
            principal=principal,
            connector=connector,
            capability_name="knowledge.ingest",
            default_limit=120,
        )
    assert caught.value.code == "capability_rate_limited"  # type: ignore[attr-defined]


def test_rest_ingest_writes_provenance_metadata(client: TestClient, monkeypatch) -> None:
    from obsion.capabilities.wecom_docs import WeComDocument

    async def _fake_fetch(**kwargs: object) -> WeComDocument:
        del kwargs
        return WeComDocument(
            document_id="docPhase73Rollback",
            title="Rollback SOP",
            content="Every release needs a rollback owner.",
            revision_id="3",
            space_id="sp_ops",
        )

    monkeypatch.setenv("OBSION_WECOM_CORP_ID", "ww-corp")
    monkeypatch.setenv("OBSION_WECOM_CORP_SECRET", "ww-secret")
    monkeypatch.setattr(
        "obsion.knowledge.wecom.fetch_authorized_wecom_document",
        _fake_fetch,
    )
    response = client.post(
        "/api/v1/knowledge/sources/wecom/documents",
        json={
            "document_id": "docPhase73Rollback",
            "acl": {"organization": True},
        },
    )
    assert response.status_code == 201, response.text
    search = client.post(
        "/api/v1/knowledge/search",
        json={"query": "rollback", "limit": 5},
    )
    assert search.status_code == 200, search.text
    hit = search.json()[0]
    assert hit["source"] == "wecom"
    assert hit["external_id"] == "docPhase73Rollback"
    assert hit["connector_name"] == "obsion-wecom-docs"
    assert hit["operation"] == "knowledge.ingest"
    assert hit["revision_id"] == "3"


def test_vendor_clients_use_shared_budget_contract() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "obsion" / "capabilities"
    for name in ("feishu_docs.py", "dingtalk_docs.py", "wecom_docs.py", "confluence.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "SyncBudgetTracker" in text
        assert "KnowledgeConnectorBudget" in text
        assert "MAX_WIKI_NODES" not in text
        assert "MAX_WORKSPACE_NODES" not in text
        assert "MAX_SPACE_NODES" not in text
        assert "MAX_PAGES = " not in text
