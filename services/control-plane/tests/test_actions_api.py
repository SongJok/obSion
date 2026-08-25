import asyncio
import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import ActionAttempt, ActionRequest
from obsion.db.session import Database
from obsion.domain.enums import ActionAttemptStatus, ActionStatus
from obsion.security.auth import get_principal
from obsion.security.identity import Principal


class _ProviderHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        idempotency_key = self.headers["Idempotency-Key"]
        previous = next(
            (item for item in self.calls if item["idempotency_key"] == idempotency_key),
            None,
        )
        if previous is None:
            if payload["parameters"].get("head") == "invalid-output":
                output = {"unexpected": True}
            else:
                output = (
                    {"external_id": "pr-42", "url": "https://git.example.test/pr/42"}
                    if payload["purpose"] == "EXECUTE"
                    else {"external_id": "pr-42", "state": "closed"}
                )
            self.calls.append(
                {
                    "idempotency_key": idempotency_key,
                    "action_id": self.headers["X-Obsion-Action-ID"],
                    "payload": payload,
                    "output": output,
                    "request_count": 1,
                }
            )
        else:
            previous["request_count"] += 1
            output = previous["output"]
        body = json.dumps(output).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def action_provider() -> tuple[str, list[dict[str, Any]]]:
    _ProviderHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/actions", _ProviderHandler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _workspace(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post("/api/v1/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _configure_action_provider(client: TestClient, endpoint: str) -> None:
    authority = endpoint.removeprefix("http://").split("/", 1)[0]
    permissions = {
        "action.pr.create",
        "action.pr.rollback",
        "action.ticket.create",
        "action.ticket.rollback",
    }
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "test-action-provider",
            "connector_type": "action-provider",
            "environment": "development",
            "endpoint": endpoint,
            "declared_grants": sorted(permissions),
            "allowed_egress": [authority],
            "status": "ACTIVE",
        },
    )
    assert connector.status_code == 201, connector.text
    connector_id = connector.json()["id"]
    capabilities = client.get("/api/v1/admin/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    action_capabilities = {
        item["name"]: item["id"]
        for item in capabilities.json()
        if item["name"]
        in {
            "action.pr.create",
            "action.pr.close",
            "action.ticket.create",
            "action.ticket.close",
        }
    }
    assert len(action_capabilities) == 4
    for capability_id in action_capabilities.values():
        bound = client.post(
            f"/api/v1/admin/capabilities/{capability_id}/bindings",
            json={
                "connector_id": connector_id,
                "environment": "development",
                "resource_selector": {},
            },
        )
        assert bound.status_code == 201, bound.text


def _create_pr_action(
    client: TestClient,
    workspace_id: str,
    *,
    idempotency_key: str,
    environment: str = "development",
    head: str = "fix/payment-timeout",
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/actions",
        json={
            "action_type": "GENERATE_PR",
            "title": "修复支付超时",
            "description": "将已经审查的补丁提交为拉取请求",
            "environment": environment,
            "target": {"repository": "obsion/payments"},
            "parameters": {
                "title": "fix: payment timeout",
                "head": head,
                "base": "main",
            },
            "rollback_parameters": {"reason": "Obsion governed rollback"},
            "idempotency_key": idempotency_key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_action(
    client: TestClient,
    action_id: str,
    expected: set[str],
    *,
    attempts: int = 160,
) -> dict[str, Any]:
    current: dict[str, Any] = {}
    for _ in range(attempts):
        response = client.get(f"/api/v1/actions/{action_id}")
        assert response.status_code == 200, response.text
        current = response.json()
        if current["action"]["status"] in expected:
            return current
        time.sleep(0.05)
    raise AssertionError(f"action did not reach {expected}: {current}")


def _approver() -> Principal:
    return Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="action-approver",
        display_name="Independent Approver",
        permissions=frozenset(
            {
                "action.approve",
                "action.approval.read",
                "workspace.read.all",
                "workspace.manage.all",
            }
        ),
    )


async def _simulate_lost_provider_response(settings: Settings, action_id: str) -> None:
    database = Database(settings)
    try:
        async with database.sessions() as session, session.begin():
            action = await session.scalar(
                select(ActionRequest).where(ActionRequest.id == UUID(action_id)).with_for_update()
            )
            assert action is not None
            attempt = await session.scalar(
                select(ActionAttempt)
                .where(ActionAttempt.action_request_id == action.id)
                .with_for_update()
            )
            assert attempt is not None
            action.status = ActionStatus.EXECUTING
            action.completed_at = None
            action.lease_owner = "crashed-worker"
            action.lease_expires_at = utc_now() - timedelta(seconds=1)
            attempt.status = ActionAttemptStatus.RUNNING
            attempt.output = {}
            attempt.completed_at = None
    finally:
        await database.dispose()


def test_action_requires_independent_approval_executes_and_rolls_back(
    client: TestClient,
    action_provider: tuple[str, list[dict[str, Any]]],
    app_settings: Settings,
) -> None:
    endpoint, provider_calls = action_provider
    _configure_action_provider(client, endpoint)
    workspace = _workspace(client, "Governed changes")
    action = _create_pr_action(
        client, workspace["id"], idempotency_key="pr-payment-timeout-20260825"
    )
    duplicate = _create_pr_action(
        client, workspace["id"], idempotency_key="pr-payment-timeout-20260825"
    )
    assert duplicate["id"] == action["id"]

    checked = client.post(
        f"/api/v1/actions/{action['id']}/preflight",
        json={"reason": "补丁已通过测试，申请创建非生产拉取请求"},
    )
    assert checked.status_code == 200, checked.text
    detail = checked.json()
    assert detail["action"]["status"] == "WAITING_APPROVAL"
    assert detail["plan"]["checksum_sha256"] == detail["action"]["plan_checksum_sha256"]
    execution_approval = detail["approvals"][0]

    self_approval = client.post(
        f"/api/v1/action-approvals/{execution_approval['id']}/approve",
        json={"reason": "self approval should fail"},
    )
    assert self_approval.status_code == 403, self_approval.text
    assert self_approval.json()["code"] == "action_self_approval_denied"

    client.app.dependency_overrides[get_principal] = _approver
    try:
        approved = client.post(
            f"/api/v1/action-approvals/{execution_approval['id']}/approve",
            json={"reason": "变更范围和回滚计划已复核"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    completed = _wait_action(client, action["id"], {"COMPLETED"})
    assert completed["action"]["result"]["execute"]["external_id"] == "pr-42"
    assert completed["attempts"][0]["status"] == "COMPLETED"
    assert len(provider_calls) == 1
    assert provider_calls[0]["payload"]["obsion"]["plan_checksum_sha256"]

    asyncio.run(_simulate_lost_provider_response(app_settings, action["id"]))
    recovered = _wait_action(client, action["id"], {"COMPLETED"})
    assert recovered["action"]["result"]["execute"]["external_id"] == "pr-42"
    assert len(provider_calls) == 1
    assert provider_calls[0]["request_count"] == 2

    rollback = client.post(
        f"/api/v1/actions/{action['id']}/rollback",
        json={"reason": "验收完成后关闭测试拉取请求"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["status"] == "WAITING_ROLLBACK_APPROVAL"
    rollback_detail = client.get(f"/api/v1/actions/{action['id']}").json()
    rollback_approval = rollback_detail["approvals"][-1]

    client.app.dependency_overrides[get_principal] = _approver
    try:
        approved_rollback = client.post(
            f"/api/v1/action-approvals/{rollback_approval['id']}/approve",
            json={"reason": "确认关闭 PR 是正确的补偿动作"},
        )
        assert approved_rollback.status_code == 200, approved_rollback.text
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    rolled_back = _wait_action(client, action["id"], {"ROLLED_BACK"})
    assert rolled_back["action"]["result"]["rollback"]["state"] == "closed"
    assert len(provider_calls) == 2
    assert provider_calls[0]["idempotency_key"] != provider_calls[1]["idempotency_key"]
    events = client.get(f"/api/v1/actions/{action['id']}/events")
    assert events.status_code == 200, events.text
    event_names = {item["name"] for item in events.json()}
    assert {"action.policy_decided", "action.completed", "action.rolled_back"}.issubset(event_names)
    inbox = client.get("/api/v1/notifications")
    assert inbox.status_code == 200, inbox.text
    assert any(item["action_request_id"] == action["id"] for item in inbox.json())


def test_production_and_deferred_actions_fail_closed(
    client: TestClient,
    action_provider: tuple[str, list[dict[str, Any]]],
) -> None:
    endpoint, provider_calls = action_provider
    _configure_action_provider(client, endpoint)
    workspace = _workspace(client, "Action boundaries")
    production = _create_pr_action(
        client,
        workspace["id"],
        idempotency_key="production-pr-boundary-20260825",
        environment="production",
    )
    checked = client.post(
        f"/api/v1/actions/{production['id']}/preflight",
        json={"reason": "This must remain blocked in the first release"},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["action"]["status"] == "PREFLIGHT_FAILED"
    assert checked.json()["action"]["error_code"] == "v1_production_action_boundary"
    assert checked.json()["plan"] is None

    deploy = client.post(
        f"/api/v1/workspaces/{workspace['id']}/actions",
        json={
            "action_type": "DEPLOY",
            "title": "部署支付服务",
            "environment": "staging",
            "target": {"service": "payments"},
            "parameters": {"version": "2026.08.25"},
            "rollback_parameters": {"version": "2026.08.24"},
            "idempotency_key": "deploy-boundary-20260825",
        },
    )
    assert deploy.status_code == 201, deploy.text
    deploy_check = client.post(
        f"/api/v1/actions/{deploy.json()['id']}/preflight",
        json={"reason": "Deployment is intentionally deferred in V1"},
    )
    assert deploy_check.status_code == 200, deploy_check.text
    assert deploy_check.json()["action"]["error_code"] == "v1_action_type_boundary"
    assert provider_calls == []


def test_action_provider_output_must_match_versioned_schema(
    client: TestClient,
    action_provider: tuple[str, list[dict[str, Any]]],
) -> None:
    endpoint, provider_calls = action_provider
    _configure_action_provider(client, endpoint)
    workspace = _workspace(client, "Strict provider contract")
    unknown_field = client.post(
        f"/api/v1/workspaces/{workspace['id']}/actions",
        json={
            "action_type": "GENERATE_PR",
            "title": "Unknown provider input",
            "environment": "development",
            "target": {"repository": "obsion/payments"},
            "parameters": {
                "title": "must fail preflight",
                "head": "invalid/unknown-field",
                "base": "main",
                "unregistered_option": True,
            },
            "rollback_parameters": {"reason": "No provider write should occur"},
            "idempotency_key": "invalid-provider-input-20260825",
        },
    )
    assert unknown_field.status_code == 201, unknown_field.text
    rejected_preflight = client.post(
        f"/api/v1/actions/{unknown_field.json()['id']}/preflight",
        json={"reason": "Reject fields outside the pinned provider schema"},
    )
    assert rejected_preflight.status_code == 200, rejected_preflight.text
    assert rejected_preflight.json()["action"]["status"] == "PREFLIGHT_FAILED"
    assert rejected_preflight.json()["action"]["error_code"] == "action_input_invalid"
    assert rejected_preflight.json()["plan"] is None
    assert provider_calls == []

    action = _create_pr_action(
        client,
        workspace["id"],
        idempotency_key="invalid-provider-output-20260825",
        head="invalid-output",
    )
    checked = client.post(
        f"/api/v1/actions/{action['id']}/preflight",
        json={"reason": "Validate the registered provider response schema"},
    )
    assert checked.status_code == 200, checked.text
    approval = checked.json()["approvals"][0]

    client.app.dependency_overrides[get_principal] = _approver
    try:
        approved = client.post(
            f"/api/v1/action-approvals/{approval['id']}/approve",
            json={"reason": "Exercise provider output validation"},
        )
        assert approved.status_code == 200, approved.text
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    failed = _wait_action(client, action["id"], {"FAILED"})
    assert failed["action"]["error_code"] == "action_output_invalid"
    assert failed["attempts"][0]["error_code"] == "action_output_invalid"
    assert len(provider_calls) == 1
