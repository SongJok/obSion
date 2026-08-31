from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.connectors import ConnectorContext
from obsion.capabilities.workflow import (
    DEVELOPMENT_CONNECTOR_TYPE,
    DEVELOPMENT_OPERATION,
    DEVELOPMENT_WORKFLOW,
    DISPATCH_OPERATION,
    DISPATCH_WORKFLOW,
    DevelopmentWorkflowExecutor,
    configured_workflow_id,
    create_automation_dispatch_handler,
    encode_workflow_call,
)
from obsion.common.errors import BudgetExceededError, ValidationError
from obsion.db.models import Connector
from obsion.domain.enums import AutomationStatus, AutomationTrigger, ConnectorStatus
from obsion.security.identity import Principal

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_REPOSITORY_ROOT = Path(__file__).parents[3]
_FORBIDDEN_WORKFLOW_IMPORTS = (
    "subprocess",
    "multiprocessing",
    "httpx",
    "requests",
    "aiohttp",
    "socket",
    "http.client",
    "urllib",
    "obsion.harness",
    "obsion.automation.worker",
)


def _principal(**overrides: object) -> Principal:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "external_id": "phase44-user",
        "display_name": "Phase 44 User",
        "roles": frozenset({"engineer"}),
        "permissions": frozenset({"workflow.invoke", "automation.trigger"}),
    }
    values.update(overrides)
    return Principal(**values)  # type: ignore[arg-type]


def _connector(**overrides: object) -> Connector:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "obsion-workflow-dispatch",
        "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
        "status": ConnectorStatus.ACTIVE,
        "environment": "development",
        "configuration": {"workflow": DISPATCH_WORKFLOW, "operation": DISPATCH_OPERATION},
        "declared_grants": ["automation.trigger"],
        "allowed_egress": [],
        "last_health": {"status": "ready"},
    }
    values.update(overrides)
    return Connector(**values)  # type: ignore[arg-type]


def _context(principal: Principal, session: object | None = None) -> ConnectorContext:
    return ConnectorContext(
        principal=principal,
        run_id=uuid4(),
        step_id=uuid4(),
        session=session,  # type: ignore[arg-type]
    )


def _executor(handler=None) -> DevelopmentWorkflowExecutor:
    executor = DevelopmentWorkflowExecutor()
    executor.register(
        DEVELOPMENT_CONNECTOR_TYPE,
        handler or create_automation_dispatch_handler(),
    )
    return executor


def test_dispatch_envelope_does_not_embed_credentials() -> None:
    encoded = encode_workflow_call(
        workflow=DISPATCH_WORKFLOW,
        operation=DISPATCH_OPERATION,
        input_payload={"service": "支付"},
    )
    assert encoded == {
        "workflow": DISPATCH_WORKFLOW,
        "operation": DISPATCH_OPERATION,
        "input": {"service": "支付"},
    }
    assert "credential" not in str(encoded).casefold()


def test_configured_workflow_id_rejects_non_uuid() -> None:
    with pytest.raises(ValidationError, match="must be a UUID") as invalid:
        configured_workflow_id({"workflow_id": "not-a-uuid"})
    assert invalid.value.code == "capability_input_invalid"
    assert configured_workflow_id({}) is None


@pytest.mark.asyncio
async def test_dispatch_handler_calls_automation_service() -> None:
    principal = _principal()
    workflow_id = uuid4()
    execution = SimpleNamespace(id=uuid4(), status=AutomationStatus.PENDING)
    service = SimpleNamespace(trigger_workflow=AsyncMock(return_value=execution))
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    result = await _executor(create_automation_dispatch_handler(service=service)).invoke(
        _connector(configuration={"workflow_id": str(workflow_id)}),
        {"input": {"service": "支付"}},
        "connector-secret-token",
        _context(principal, session),
    )
    assert result.data["dispatched"] is True
    assert result.data["execution_id"] == str(execution.id)
    assert result.data["status"] == AutomationStatus.PENDING.value
    assert result.data["workflow_id"] == str(workflow_id)
    assert "connector-secret-token" not in str(result.data)
    service.trigger_workflow.assert_awaited_once()
    kwargs = service.trigger_workflow.await_args
    assert kwargs.args[1] is principal
    assert kwargs.args[2] == workflow_id
    assert kwargs.kwargs["trigger"] is AutomationTrigger.CAPABILITY
    assert kwargs.kwargs["input_payload"] == {"service": "支付"}
    assert str(workflow_id) in kwargs.kwargs["idempotency_key"]


@pytest.mark.asyncio
async def test_dispatch_without_gateway_session_fails_closed() -> None:
    workflow_id = uuid4()
    with pytest.raises(ValidationError, match="Gateway session") as missing:
        await _executor().invoke(
            _connector(configuration={"workflow_id": str(workflow_id)}),
            {"input": {}},
            None,
            _context(_principal()),
        )
    assert missing.value.code == "capability_input_invalid"


@pytest.mark.asyncio
async def test_nested_analysis_dispatch_is_budget_exhausted() -> None:
    principal = _principal()
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=uuid4())
    with pytest.raises(BudgetExceededError) as nested:
        await _executor().invoke(
            _connector(configuration={"workflow_id": str(uuid4())}),
            {"input": {}},
            None,
            _context(principal, session),
        )
    assert nested.value.code == "budget_exceeded"
    assert nested.value.details["budget"] == "workflow_dispatch_depth"


@pytest.mark.asyncio
async def test_dispatch_handler_still_echoes_without_workflow_id() -> None:
    principal = _principal()
    result = await _executor().invoke(
        _connector(
            configuration={"workflow": DEVELOPMENT_WORKFLOW, "operation": DEVELOPMENT_OPERATION},
            declared_grants=["workflow.invoke"],
        ),
        {
            "workflow": DEVELOPMENT_WORKFLOW,
            "operation": DEVELOPMENT_OPERATION,
            "input": {"ping": "pong"},
        },
        None,
        _context(principal),
    )
    assert result.data["echo"] == {"ping": "pong"}
    assert "dispatched" not in result.data


def test_seeded_trigger_capability_is_catalogued(client: TestClient) -> None:
    capabilities = client.get("/api/v1/admin/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    trigger = next(
        item for item in capabilities.json() if item["name"] == "workflow.automation.trigger"
    )
    assert trigger["transport"] == "WORKFLOW"
    assert trigger["permission"] == "automation.trigger"


def test_workflow_dispatch_is_not_declared_on_shipped_agents() -> None:
    builtins = (_SOURCE_ROOT / "registry" / "builtins.py").read_text(encoding="utf-8")
    assert "_IN_PROCESS_ADAPTER_CAPABILITIES" in builtins
    assert "workflow.automation.trigger" in builtins
    for path in (_REPOSITORY_ROOT / "agents").glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "workflow.automation.trigger" not in text
        assert "obsion-workflow-dispatch" not in text


def test_gateway_dispatch_creates_automation_execution(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Workflow dispatch", "description": "Gateway to AutomationService"},
    )
    assert workspace.status_code == 201, workspace.text
    created = client.post(
        f"/api/v1/workspaces/{workspace.json()['id']}/workflows",
        json={
            "name": "gateway-dispatch-review",
            "display_name": "网关触发人工确认",
            "concurrency_policy": "ALLOW",
            "max_concurrency": 4,
            "spec": {
                "steps": [
                    {
                        "id": "review",
                        "name": "人工确认",
                        "type": "HUMAN_REVIEW",
                        "review_instructions": "确认网关触发是否可继续。",
                    }
                ]
            },
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["workflow"]["id"]
    assert client.post(f"/api/v1/workflows/{workflow_id}/versions/1/publish").status_code == 200
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "obsion-workflow-dispatch-test",
            "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
            "environment": "development",
            "status": "ACTIVE",
            "declared_grants": ["automation.trigger"],
            "allowed_egress": [],
            "configuration": {"workflow_id": workflow_id},
        },
    )
    assert connector.status_code == 201, connector.text
    capabilities = client.get("/api/v1/admin/capabilities")
    trigger = next(
        item for item in capabilities.json() if item["name"] == "workflow.automation.trigger"
    )
    binding = client.post(
        f"/api/v1/admin/capabilities/{trigger['id']}/bindings",
        json={"connector_id": connector.json()["id"], "environment": "development"},
    )
    assert binding.status_code == 201, binding.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Dispatch from Gateway"},
    )
    assert thread.status_code == 201, thread.text
    turn = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "Trigger the governed workflow from the capability gateway."},
    )
    assert turn.status_code == 202, turn.text
    run_id = turn.json()["run"]["id"]
    invoked = client.post(
        "/api/v1/capabilities/workflow.automation.trigger/invoke",
        json={
            "run_id": run_id,
            "payload": {"input": {"service": "支付"}},
            "environment": "development",
        },
    )
    assert invoked.status_code == 200, invoked.text
    body = invoked.json()
    assert body["status"] == "COMPLETED", body
    assert body["output"]["dispatched"] is True
    assert body["output"]["workflow_id"] == workflow_id
    assert body["evidence_id"]
    execution_id = body["output"]["execution_id"]
    execution: dict = {}
    for _ in range(120):
        response = client.get(f"/api/v1/automation/executions/{execution_id}")
        assert response.status_code == 200, response.text
        execution = response.json()
        if execution["status"] in {"WAITING_REVIEW", "COMPLETED"}:
            break
        time.sleep(0.05)
    assert execution["trigger"] == AutomationTrigger.CAPABILITY.value
    assert execution["workflow_id"] == workflow_id
    assert execution["status"] in {"WAITING_REVIEW", "PENDING", "RUNNING", "COMPLETED"}


def test_workflow_dispatch_is_not_a_second_orchestrator() -> None:
    workflow_source = (_SOURCE_ROOT / "capabilities" / "workflow.py").read_text(encoding="utf-8")
    tree = ast.parse(workflow_source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    violations = [
        imported
        for imported in imports
        if imported in _FORBIDDEN_WORKFLOW_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_WORKFLOW_IMPORTS)
    ]
    assert violations == []
    assert "AutomationService" in workflow_source
    assert "AutomationTrigger.CAPABILITY" in workflow_source
    main = (_SOURCE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "create_automation_dispatch_handler()" in main
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "DevelopmentWorkflowExecutor" not in runtime
    assert "capabilities.workflow" not in runtime
    worker = (_SOURCE_ROOT / "automation" / "worker.py").read_text(encoding="utf-8")
    assert "DevelopmentWorkflowExecutor" not in worker
    assert "create_automation_dispatch_handler" not in worker
