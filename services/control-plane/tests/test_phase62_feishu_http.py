from __future__ import annotations

import ast
import hashlib
import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
IM_ROOT = Path(__file__).resolve().parents[3] / "apps" / "im-adapter" / "src" / "obsion_im"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run_id}")


def _completed_feishu_run(client: TestClient) -> str:
    user = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "phase62-feishu-user",
            "email": "phase62-feishu@obsion.dev",
            "display_name": "Phase 62 Feishu",
            "attributes": {},
        },
    )
    assert user.status_code == 201, user.text
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": "feishu",
            "sender_id": "ou_phase62",
            "user_id": user.json()["id"],
        },
    )
    assert binding.status_code == 201, binding.text
    accepted = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": "feishu",
            "sender_id": "ou_phase62",
            "conversation_id": "oc_phase62",
            "text": "你好",
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run_id"]
    assert _wait_terminal(client, run_id)["status"] == "COMPLETED"
    return run_id


def test_feishu_delivery_is_policy_authorized_idempotent_and_audited(
    client: TestClient,
) -> None:
    run_id = _completed_feishu_run(client)
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert prepared.status_code == 200, prepared.text
    payload = prepared.json()
    assert payload["channel"] == "feishu"
    assert payload["conversation_id"] == "oc_phase62"
    assert payload["status"] == "PENDING"
    assert payload["idempotency_key"] == payload["id"]
    assert payload["content_fingerprint"] == hashlib.sha256(payload["text"].encode()).hexdigest()
    assert payload["text"].strip()
    assert "你好" in payload["text"]

    completed = client.post(
        f"/api/v1/experience/im/deliveries/{payload['id']}/complete",
        json={"vendor_message_id": "om_phase62"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "SENT"
    assert completed.json()["vendor_message_id"] == "om_phase62"

    repeated = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == payload["id"]
    assert repeated.json()["status"] == "SENT"
    idempotent = client.post(
        f"/api/v1/experience/im/deliveries/{payload['id']}/complete",
        json={"vendor_message_id": "om_phase62"},
    )
    assert idempotent.status_code == 200, idempotent.text

    audit = client.get("/api/v1/admin/audit?limit=200")
    assert audit.status_code == 200, audit.text
    actions = {item["action"] for item in audit.json()}
    assert "experience.im.delivery.prepare" in actions
    assert "experience.im.delivery.complete" in actions


def test_feishu_delivery_failure_is_retryable_with_the_same_id(client: TestClient) -> None:
    run_id = _completed_feishu_run(client)
    prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries").json()
    failed = client.post(
        f"/api/v1/experience/im/deliveries/{prepared['id']}/fail",
        json={"failure_code": "vendor_request_failed"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "FAILED"
    retried = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == prepared["id"]
    assert retried.json()["status"] == "PENDING"
    assert retried.json()["attempt_count"] == 2


def test_non_im_run_cannot_be_delivered(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "No IM delivery", "description": "Regular greeting"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Greeting"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED"
    response = client.post(f"/api/v1/experience/im/runs/{run['id']}/deliveries")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "im_delivery_context_missing"


def test_feishu_delivery_is_tenant_scoped(client: TestClient) -> None:
    run_id = _completed_feishu_run(client)
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-im-delivery",
        display_name="Cross-tenant IM",
        permissions=frozenset({"im.delegate", "workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        response = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
        assert response.status_code == 404, response.text
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_feishu_http_is_explicit_and_not_a_second_harness() -> None:
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "feishu-http" in admin
    assert "im.reply.deliver" in admin
    assert "open.feishu.cn" in admin
    assert "--deliver http" in admin
    feishu = ast.parse((IM_ROOT / "feishu.py").read_text(encoding="utf-8"))
    imports = [
        node.module
        for node in ast.walk(feishu)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.harness.runtime" not in imports
    assert "obsion.db.models" not in imports
    webhook = (IM_ROOT / "webhook.py").read_text(encoding="utf-8")
    assert "may only bind 127.0.0.1" in webhook
    config = (IM_ROOT / "config.py").read_text(encoding="utf-8")
    assert "Generic HTTP delivery is not implemented" in config
