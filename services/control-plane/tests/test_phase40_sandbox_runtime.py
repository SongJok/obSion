from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest, GatewayStatus
from obsion.db.models import AgentVersion, CapabilityDefinition, CapabilityVersion, Connector
from obsion.domain.enums import (
    CapabilityTransport,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.harness.planner import Planner
from obsion.registry.agent_spec import (
    ALLOWED_SANDBOX_MOUNTS,
    AgentSpec,
    normalize_sandbox,
    sandbox_allows_capabilities,
)
from obsion.registry.manifests import RegistryManifestError
from obsion.security.identity import Principal
from obsion.security.policy import Decision, PolicyEngine, PolicyInput

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_FORBIDDEN_SANDBOX_RUNTIME = ("docker", "gvisor", "kubernetes", "containerd")


def _spec(**overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "description": "Sandbox probe",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 8,
        "capabilities": ["knowledge.search"],
        "riskPolicy": {"maxLevel": "L1"},
    }
    spec.update(overrides)
    return spec


def _principal() -> Principal:
    return Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="phase40-user",
        display_name="Phase 40 User",
        roles=frozenset({"engineer"}),
        permissions=frozenset({"knowledge.read"}),
    )


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    run: dict = {}
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_agent_spec_normalizes_default_sandbox_mounts() -> None:
    parsed = AgentSpec.from_dict(_spec())
    assert parsed.sandbox["enabled"] is True
    assert parsed.sandbox["network"] == "gateway-only"
    assert parsed.sandbox["mounts"] == list(ALLOWED_SANDBOX_MOUNTS)
    assert sandbox_allows_capabilities(parsed.sandbox)


def test_agent_spec_accepts_network_deny_without_capabilities() -> None:
    parsed = AgentSpec.from_dict(_spec(sandbox={"enabled": True, "network": "deny"}))
    assert parsed.sandbox["network"] == "deny"
    assert sandbox_allows_capabilities(parsed.sandbox) is False


@pytest.mark.parametrize(
    ("sandbox", "match"),
    [
        ({"enabled": False, "network": "gateway-only"}, "sandbox.enabled"),
        ({"enabled": True, "network": "unrestricted"}, "sandbox.network"),
        ({"enabled": True, "network": "gateway-only", "privileged": True}, "privileged"),
        ({"enabled": True, "network": "gateway-only", "docker": True}, "docker"),
        (
            {"enabled": True, "network": "gateway-only", "mounts": ["/"]},
            "sandbox.mounts",
        ),
        (
            {
                "enabled": True,
                "network": "gateway-only",
                "mounts": ["/workspace", "/etc"],
            },
            "sandbox.mounts",
        ),
        ({"enabled": True, "network": "gateway-only", "cpuMillis": 0}, "cpuMillis"),
    ],
)
def test_agent_spec_rejects_escape_sandbox_shapes(sandbox: dict, match: str) -> None:
    with pytest.raises(RegistryManifestError, match=match):
        AgentSpec.from_dict(_spec(sandbox=sandbox))


def test_normalize_sandbox_pins_declared_resource_limits() -> None:
    sandbox = normalize_sandbox(
        {
            "network": "gateway-only",
            "cpuMillis": 500,
            "memoryMb": 256,
            "diskMb": 1024,
            "processLimit": 32,
            "mounts": ["/workspace", "/artifacts"],
        },
        source="AgentSpec",
    )
    assert sandbox["cpuMillis"] == 500
    assert sandbox["memoryMb"] == 256
    assert sandbox["mounts"] == ["/workspace", "/artifacts"]


def test_planner_omits_capability_steps_when_network_is_denied() -> None:
    plan = Planner().create(
        {"route": "KNOWLEDGE", "question": "release policy"},
        available_capabilities=frozenset(),
    )
    assert plan.steps == ()
    assert plan.required_evidence == ("DOCUMENT",)


def test_completed_run_pins_sandbox_on_plan(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 40 sandbox", "description": "Pin sandbox on plan"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Phase 40 sandbox"},
    )
    assert thread.status_code == 201, thread.text
    turn = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert turn.status_code == 202, turn.text
    run = _wait_terminal(client, str(turn.json()["run"]["id"]))
    assert run["status"] == "COMPLETED"
    sandbox = run["plan"]["sandbox"]
    assert sandbox["network"] == "gateway-only"
    assert sandbox["enabled"] is True
    assert sandbox["mounts"] == list(ALLOWED_SANDBOX_MOUNTS)
    assert "available_capabilities" in run["plan"]


@pytest.mark.asyncio
async def test_gateway_denies_capability_when_sandbox_network_is_deny() -> None:
    principal = _principal()
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=principal.organization_id,
        name="phase40.read",
        display_name="Phase 40 read",
        description="test",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.INTERNAL,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="knowledge.read",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        evidence_mapping={"type": "DOCUMENT"},
        timeout_seconds=5,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )
    connector = Connector(
        id=uuid4(),
        organization_id=principal.organization_id,
        name="phase40-connector",
        connector_type="test",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        configuration={},
        declared_grants=["knowledge.read"],
        allowed_egress=[],
        last_health={"status": "ready"},
    )
    agent_version = AgentVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        agent_id=uuid4(),
        version=1,
        spec={
            "capabilities": [definition.name],
            "riskPolicy": {"maxLevel": "L2"},
            "sandbox": {"enabled": True, "network": "deny"},
        },
        checksum_sha256="0" * 64,
        created_by=principal.id,
        created_at=datetime.now(UTC),
    )
    calls = {"executor": 0}

    class _Executor:
        async def invoke(self, connector, payload, credential, context):
            del connector, payload, credential, context
            calls["executor"] += 1
            raise AssertionError("sandbox deny must not reach the executor")

    async def evaluate(_session, request: PolicyInput) -> Decision:
        if request.agent_capability_allowed is False:
            return Decision(
                id=uuid4(),
                effect=DecisionEffect.DENY,
                reason_codes=("agent_capability_not_allowed",),
            )
        return Decision(id=uuid4(), effect=DecisionEffect.ALLOW)

    policy = PolicyEngine()
    policy.evaluate = evaluate  # type: ignore[method-assign]
    gateway = CapabilityGateway(
        {CapabilityTransport.INTERNAL.value: _Executor()},
        policy=policy,
    )
    gateway._resolve = AsyncMock(return_value=(definition, version, connector))  # noqa: SLF001
    gateway.events = SimpleNamespace(append=AsyncMock())
    gateway._policy_event = AsyncMock()  # noqa: SLF001
    gateway._gateway_event = AsyncMock()  # noqa: SLF001
    gateway._audit = AsyncMock()  # noqa: SLF001
    session = AsyncMock()
    session.scalar.return_value = agent_version
    result = await gateway.invoke(
        session,
        GatewayRequest(
            principal=principal,
            capability_name=definition.name,
            payload={},
            resource={},
            environment="development",
            agent_name="deny-agent",
            agent_version_id=agent_version.id,
            run_id=uuid4(),
        ),
    )
    assert result.status == GatewayStatus.DENIED
    assert result.error_code == "capability_denied"
    assert calls["executor"] == 0


@pytest.mark.asyncio
async def test_agent_capability_allowed_is_false_for_unrestricted_network() -> None:
    principal = _principal()
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        capability_id=uuid4(),
        version=1,
        transport=CapabilityTransport.INTERNAL,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="knowledge.read",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        evidence_mapping={"type": "DOCUMENT"},
        timeout_seconds=5,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )
    agent_version = AgentVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        agent_id=uuid4(),
        version=1,
        spec={
            "capabilities": ["phase40.read"],
            "riskPolicy": {"maxLevel": "L2"},
            "sandbox": {"network": "unrestricted"},
        },
        checksum_sha256="0" * 64,
        created_by=principal.id,
        created_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.scalar.return_value = agent_version
    allowed = await CapabilityGateway._agent_capability_allowed(  # noqa: SLF001
        session,
        GatewayRequest(
            principal=principal,
            capability_name="phase40.read",
            payload={},
            resource={},
            environment="development",
            agent_name="escape-agent",
            agent_version_id=agent_version.id,
            run_id=uuid4(),
        ),
        "phase40.read",
        version,
    )
    assert allowed is False


def test_workbench_exposes_pinned_sandbox_without_an_agent_picker() -> None:
    web = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"
    inspector = (web / "components" / "runtime-inspector.tsx").read_text(encoding="utf-8")
    composer = (web / "components" / "composer.tsx").read_text(encoding="utf-8")
    admin = (web / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "sandbox-chip" in inspector
    assert "run.plan.sandbox" in inspector
    assert "沙箱网络仅经 Gateway" in admin
    assert "selectedAgent" not in composer
    assert "agent-picker" not in composer.casefold()


def test_harness_does_not_claim_container_isolation() -> None:
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    for name in _FORBIDDEN_SANDBOX_RUNTIME:
        assert f"import {name}" not in runtime
        assert f"from {name}" not in runtime
    assert 'plan_payload["sandbox"]' in runtime
    gateway = (_SOURCE_ROOT / "capabilities" / "gateway.py").read_text(encoding="utf-8")
    assert "sandbox_allows_capabilities" in gateway
