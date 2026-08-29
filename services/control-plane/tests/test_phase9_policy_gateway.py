import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from obsion.capabilities.connectors import ConnectorResult
from obsion.capabilities.gateway import (
    CapabilityGateway,
    GatewayRequest,
    GatewayStatus,
)
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
from obsion.security.identity import Principal
from obsion.security.policy import Decision, PolicyEngine, PolicyInput


class _Executor:
    def __init__(self, result: ConnectorResult | None = None, *, delay: float = 0) -> None:
        self.result = result or ConnectorResult(
            data={"answer": "ok"},
            source="test-connector",
            resource="test://resource",
        )
        self.delay = delay
        self.calls = 0

    async def invoke(self, connector, payload, credential, context) -> ConnectorResult:
        del connector, payload, credential, context
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


class _RateLimiter:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def allow(self, key: str, limit: int | None = None) -> bool:
        del key, limit
        return self.allowed

    async def aclose(self) -> None:
        return None


def _fixture_models(
    *,
    risk: RiskLevel = RiskLevel.L1,
    output_schema: dict | None = None,
    timeout_seconds: int = 1,
    grants: list[str] | None = None,
) -> tuple[Principal, CapabilityDefinition, CapabilityVersion, Connector]:
    organization_id = uuid4()
    principal = Principal(
        id=uuid4(),
        organization_id=organization_id,
        external_id="phase9-user",
        display_name="Phase 9 User",
        department_id=uuid4(),
        department="Platform",
        roles=frozenset({"engineer"}),
        permissions=frozenset({"knowledge.read"}),
        attributes={"region": "cn"},
    )
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=organization_id,
        name="phase9.read",
        display_name="Phase 9 read",
        description="A test read capability",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.INTERNAL,
        risk_level=risk,
        side_effect=SideEffect.NONE,
        permission_action="knowledge.read",
        input_schema={"type": "object"},
        output_schema=output_schema or {"type": "object"},
        evidence_mapping={"type": "DOCUMENT"},
        timeout_seconds=timeout_seconds,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )
    connector = Connector(
        id=uuid4(),
        organization_id=organization_id,
        name="phase9-connector",
        connector_type="test",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        configuration={},
        declared_grants=grants if grants is not None else ["knowledge.read"],
        allowed_egress=[],
        last_health={"status": "ready"},
    )
    return principal, definition, version, connector


def _request(principal: Principal, *, payload: dict | None = None) -> GatewayRequest:
    return GatewayRequest(
        principal=principal,
        capability_name="phase9.read",
        payload=payload or {},
        resource={"environment": "development", "service": "catalog"},
        environment="development",
        agent_name="general-agent",
        run_id=uuid4(),
    )


def test_harness_and_capability_api_have_no_executor_bypass() -> None:
    source_root = Path(__file__).parents[1] / "src" / "obsion"
    runtime = (source_root / "harness" / "runtime.py").read_text(encoding="utf-8")
    capability_api = (source_root / "api" / "capabilities.py").read_text(encoding="utf-8")

    assert "self.gateway.invoke" in runtime
    assert "executor.invoke" not in runtime
    assert "ConnectorExecutor" not in runtime
    assert "gateway.invoke" in capability_api
    assert "capabilities.connectors" not in capability_api


def _decision(effect: DecisionEffect, *, obligations: tuple[dict, ...] = ()) -> Decision:
    return Decision(id=uuid4(), effect=effect, obligations=obligations)


def _gateway(
    principal: Principal,
    definition: CapabilityDefinition,
    version: CapabilityVersion,
    connector: Connector,
    executor: _Executor,
    decision: Decision,
    *,
    limiter: _RateLimiter | None = None,
) -> CapabilityGateway:
    policy = PolicyEngine()
    policy.evaluate = AsyncMock(return_value=decision)  # type: ignore[method-assign]
    gateway = CapabilityGateway(
        {CapabilityTransport.INTERNAL.value: executor},
        policy=policy,
        rate_limiter=limiter,
    )
    gateway._resolve = AsyncMock(return_value=(definition, version, connector))  # noqa: SLF001
    gateway.events = SimpleNamespace(append=AsyncMock())
    gateway._policy_event = AsyncMock()  # noqa: SLF001
    gateway._gateway_event = AsyncMock()  # noqa: SLF001
    gateway._audit = AsyncMock()  # noqa: SLF001
    gateway._evidence = AsyncMock(return_value=SimpleNamespace(id=uuid4()))  # noqa: SLF001
    return gateway


@pytest.mark.asyncio
async def test_policy_conditions_cover_user_department_role_agent_resource_context() -> None:
    principal, _definition, version, _connector = _fixture_models()
    policy = SimpleNamespace(
        id=uuid4(),
        name="who-policy",
        version=1,
        effect=DecisionEffect.ALLOW,
        conditions={
            "actions": ["knowledge.*"],
            "users": [str(principal.id)],
            "departments": [str(principal.department_id)],
            "roles_any": ["engineer"],
            "agents": ["general-*"],
            "attributes": {"region": "cn"},
            "resource": {"service": "catalog"},
            "context": {"environment": "development"},
            "max_risk": "L1",
        },
        obligations=[],
    )
    request = PolicyInput(
        principal=principal,
        capability=version,
        action="knowledge.read",
        resource={"service": "catalog"},
        context={"environment": "development"},
        agent_name="general-agent",
        agent_version_id=uuid4(),
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalars.return_value = [policy]
    effect, _obligations, reasons, _ids = await PolicyEngine()._resolve(session, request)  # noqa: SLF001
    assert effect == DecisionEffect.ALLOW
    assert reasons == ["policy:who-policy:v1"]


@pytest.mark.asyncio
async def test_policy_allow_cannot_elevate_a_principal_without_permission() -> None:
    principal, _definition, version, _connector = _fixture_models()
    principal = Principal(
        id=principal.id,
        organization_id=principal.organization_id,
        external_id=principal.external_id,
        display_name=principal.display_name,
        department_id=principal.department_id,
        department=principal.department,
        roles=principal.roles,
        permissions=frozenset(),
        attributes=principal.attributes,
    )
    policy = SimpleNamespace(
        id=uuid4(),
        name="broad-allow",
        version=1,
        effect=DecisionEffect.ALLOW,
        conditions={"actions": ["knowledge.read"]},
        obligations=[],
    )
    session = AsyncMock()
    session.scalars.return_value = [policy]
    effect, _obligations, reasons, _ids = await PolicyEngine()._resolve(  # noqa: SLF001
        session,
        PolicyInput(
            principal=principal,
            capability=version,
            action="knowledge.read",
            resource={},
            context={},
            agent_name="general-agent",
        ),
    )
    assert effect == DecisionEffect.DENY
    assert reasons == ["no_matching_grant"]


@pytest.mark.asyncio
async def test_gateway_rechecks_pinned_agent_capabilities_and_risk_budget() -> None:
    principal, definition, version, connector = _fixture_models(risk=RiskLevel.L2)
    agent_version = AgentVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        agent_id=uuid4(),
        version=1,
        spec={
            "capabilities": [definition.name],
            "riskPolicy": {"maxLevel": "L1"},
        },
        checksum_sha256="0" * 64,
        created_by=principal.id,
        created_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.scalar.return_value = agent_version
    request = replace(_request(principal), agent_version_id=agent_version.id)
    allowed = await CapabilityGateway._agent_capability_allowed(  # noqa: SLF001
        session, request, definition.name, version
    )
    assert allowed is False


@pytest.mark.asyncio
async def test_denied_policy_never_validates_or_calls_executor() -> None:
    principal, definition, version, connector = _fixture_models()
    executor = _Executor()
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.DENY),
    )

    result = await gateway.invoke(AsyncMock(), _request(principal, payload={"invalid": object()}))

    assert result.status == GatewayStatus.DENIED
    assert result.error_code == "capability_denied"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_l5_is_denied_and_connector_grant_is_mandatory() -> None:
    principal, definition, version, connector = _fixture_models(risk=RiskLevel.L5)
    executor = _Executor()
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.ALLOW),
    )
    # A policy adapter cannot override the hard L5 boundary; use the real resolver
    # for that assertion.
    gateway.policy = PolicyEngine()
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalars.return_value = []
    decision = await gateway.policy.evaluate(
        session,
        PolicyInput(
            principal=principal,
            capability=version,
            action=version.permission_action,
            resource={},
            context={},
            agent_name="general-agent",
        ),
    )
    assert decision.effect == DecisionEffect.DENY

    principal, definition, version, connector = _fixture_models(grants=[])
    executor = _Executor()
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.ALLOW),
    )
    result = await gateway.invoke(AsyncMock(), _request(principal))
    assert result.status == GatewayStatus.DENIED
    assert result.error_code == "connector_grant_missing"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_ask_waits_for_approval_and_mask_applies_before_evidence() -> None:
    principal, definition, version, connector = _fixture_models()
    executor = _Executor(ConnectorResult({"secret": "hidden", "rows": [1, 2]}, "src", "res"))
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.ASK),
    )
    gateway._find_approval = AsyncMock(return_value=None)  # noqa: SLF001
    gateway._create_approval = AsyncMock(return_value=SimpleNamespace(id=uuid4()))  # noqa: SLF001
    waiting = await gateway.invoke(AsyncMock(), _request(principal))
    assert waiting.status == GatewayStatus.WAITING_APPROVAL
    assert waiting.approval_id is not None
    assert executor.calls == 0

    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(
            DecisionEffect.MASK,
            obligations=(
                {"type": "mask_fields", "fields": ["secret"]},
                {"type": "limit_result_rows", "value": 1},
            ),
        ),
    )
    completed = await gateway.invoke(AsyncMock(), _request(principal))
    assert completed.status == GatewayStatus.COMPLETED
    evidence_args = gateway._evidence.await_args.args  # noqa: SLF001
    assert evidence_args[5] == {"secret": "***", "rows": [1]}


@pytest.mark.asyncio
async def test_rate_limit_and_timeout_stop_execution_with_typed_results() -> None:
    principal, definition, version, connector = _fixture_models()
    executor = _Executor()
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.ALLOW),
        limiter=_RateLimiter(False),
    )
    limited = await gateway.invoke(AsyncMock(), _request(principal))
    assert limited.status == GatewayStatus.DENIED
    assert limited.error_code == "capability_rate_limited"
    assert executor.calls == 0

    principal, definition, version, connector = _fixture_models(timeout_seconds=1)
    executor = _Executor(delay=0.05)
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.ALLOW),
    )
    version.timeout_seconds = 0  # type: ignore[assignment]
    timed_out = await gateway.invoke(AsyncMock(), _request(principal))
    assert timed_out.status == GatewayStatus.FAILED
    assert timed_out.error_code == "capability_timeout"


@pytest.mark.asyncio
async def test_connector_validation_error_is_not_hidden_as_generic_failure() -> None:
    principal, definition, version, connector = _fixture_models(
        output_schema={"type": "object", "required": ["answer"]}
    )
    executor = _Executor(ConnectorResult({}, "src", "res"))
    gateway = _gateway(
        principal,
        definition,
        version,
        connector,
        executor,
        _decision(DecisionEffect.ALLOW),
    )
    result = await gateway.invoke(AsyncMock(), _request(principal))
    assert result.status == GatewayStatus.FAILED
    assert result.error_code == "capability_output_invalid"
