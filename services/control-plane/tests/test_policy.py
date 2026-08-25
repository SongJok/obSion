from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from obsion.db.models import CapabilityVersion, Policy
from obsion.domain.enums import (
    CapabilityTransport,
    Classification,
    DecisionEffect,
    RiskLevel,
    SideEffect,
)
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, PolicyInput


def capability(*, risk: RiskLevel, side_effect: SideEffect = SideEffect.NONE) -> CapabilityVersion:
    return CapabilityVersion(
        id=uuid4(),
        organization_id=uuid4(),
        capability_id=uuid4(),
        version=1,
        transport=CapabilityTransport.HTTP,
        risk_level=risk,
        side_effect=side_effect,
        permission_action="resource.read",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        evidence_mapping={},
        timeout_seconds=10,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )


def request(version: CapabilityVersion, *, permissions: frozenset[str]) -> PolicyInput:
    principal = Principal(
        id=uuid4(),
        organization_id=version.organization_id,
        external_id="tester",
        display_name="Tester",
        permissions=permissions,
        roles=frozenset({"analyst"}),
    )
    return PolicyInput(
        principal=principal,
        capability=version,
        action="resource.read",
        resource={"environment": "production"},
        context={"environment": "production"},
        agent_name="general-agent",
    )


@pytest.mark.parametrize(
    "version",
    [
        capability(risk=RiskLevel.L3),
        capability(risk=RiskLevel.L1, side_effect=SideEffect.WRITE),
        capability(risk=RiskLevel.L1, side_effect=SideEffect.DESTRUCTIVE),
    ],
)
async def test_immutable_v1_boundary_denies_high_risk_and_writes(
    version: CapabilityVersion,
) -> None:
    effect, _obligations, reasons, _ids = await PolicyEngine()._resolve(  # noqa: SLF001
        AsyncMock(), request(version, permissions=frozenset({"*"}))
    )
    assert effect == DecisionEffect.DENY
    assert reasons == ["v1_read_only_boundary"]


async def test_l2_reads_are_masked_by_default() -> None:
    session = AsyncMock()
    session.scalars.return_value = []
    effect, obligations, reasons, _ids = await PolicyEngine()._resolve(  # noqa: SLF001
        session, request(capability(risk=RiskLevel.L2), permissions=frozenset({"resource.read"}))
    )
    assert effect == DecisionEffect.MASK
    assert {item["type"] for item in obligations} == {
        "mask_classified_fields",
        "limit_result_rows",
    }
    assert reasons == ["default_sensitive_read"]


async def test_explicit_deny_wins_over_allow() -> None:
    version = capability(risk=RiskLevel.L1)
    now = datetime.now(UTC)
    allow = Policy(
        id=uuid4(),
        organization_id=version.organization_id,
        name="allow-read",
        version=1,
        priority=100,
        effect=DecisionEffect.ALLOW,
        enabled=True,
        conditions={"actions": ["resource.read"]},
        obligations=[],
        reason="Allowed",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    deny = Policy(
        id=uuid4(),
        organization_id=version.organization_id,
        name="deny-production",
        version=1,
        priority=10,
        effect=DecisionEffect.DENY,
        enabled=True,
        conditions={"actions": ["resource.read"], "context": {"environment": "production"}},
        obligations=[],
        reason="Production denied",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    session = AsyncMock()
    session.scalars.return_value = [allow, deny]
    effect, _obligations, reasons, ids = await PolicyEngine()._resolve(  # noqa: SLF001
        session, request(version, permissions=frozenset({"resource.read"}))
    )
    assert effect == DecisionEffect.DENY
    assert reasons == ["policy:deny-production:v1"]
    assert ids == [deny.id]
