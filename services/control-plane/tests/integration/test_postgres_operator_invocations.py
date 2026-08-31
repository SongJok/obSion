import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from obsion.common.time import utc_now
from obsion.config import get_settings
from obsion.db.models import (
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    OperatorCapabilityInvocation,
    Organization,
    PolicyDecision,
    User,
)
from obsion.domain.enums import (
    CapabilityTransport,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.persistence.operator_invocations import OperatorInvocationStore
from obsion.security.identity import Principal


@pytest.mark.asyncio
async def test_operator_invocation_claim_is_concurrent_replayable_and_immutable() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    organization_id = uuid4()
    principal_id = uuid4()
    capability_id = uuid4()
    capability_version_id = uuid4()
    connector_id = uuid4()
    policy_decision_id = uuid4()
    request_id = uuid4()
    now = utc_now()
    principal = Principal(
        id=principal_id,
        organization_id=organization_id,
        external_id="phase79-postgres",
        display_name="Phase 79 PostgreSQL",
        permissions=frozenset({"knowledge.write"}),
    )

    async with sessions() as session, session.begin():
        session.add(
            Organization(
                id=organization_id,
                slug=f"phase79-{organization_id}",
                name="Phase 79",
                active=True,
                settings={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            User(
                id=principal_id,
                organization_id=organization_id,
                external_id=principal.external_id,
                email=f"{principal_id}@example.invalid",
                display_name=principal.display_name,
                active=True,
                attributes={},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            CapabilityDefinition(
                id=capability_id,
                organization_id=organization_id,
                name=f"phase79.{capability_id}",
                display_name="Phase 79",
                description="PostgreSQL operator invocation invariant",
                status=RegistryStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Connector(
                id=connector_id,
                organization_id=organization_id,
                name=f"phase79-{connector_id}",
                connector_type="phase79",
                status=ConnectorStatus.ACTIVE,
                environment="development",
                configuration={},
                declared_grants=["knowledge.write"],
                allowed_egress=[],
                last_health={},
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            CapabilityVersion(
                id=capability_version_id,
                organization_id=organization_id,
                capability_id=capability_id,
                version=1,
                transport=CapabilityTransport.HTTP,
                risk_level=RiskLevel.L2,
                side_effect=SideEffect.IDEMPOTENT_WRITE,
                permission_action="knowledge.write",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                evidence_mapping={"type": "DOCUMENT"},
                timeout_seconds=30,
                data_classification=Classification.INTERNAL,
                checksum_sha256="a" * 64,
                created_at=now,
            )
        )
        session.add(
            PolicyDecision(
                id=policy_decision_id,
                organization_id=organization_id,
                run_id=None,
                principal_id=principal_id,
                agent_version_id=None,
                capability_version_id=capability_version_id,
                action="knowledge.write",
                resource={"source": "feishu"},
                context={"invocation_mode": "operator"},
                risk_level=RiskLevel.L2,
                effect=DecisionEffect.MASK,
                matched_policy_ids=[],
                obligations=[],
                reason_codes=["phase79"],
                input_fingerprint="b" * 64,
                created_at=now,
            )
        )

    async def claim() -> tuple[str, UUID]:
        async with sessions() as session, session.begin():
            result = await OperatorInvocationStore().claim(
                session,
                principal,
                request_id=request_id,
                capability_name="knowledge.ingest",
                capability_version_id=capability_version_id,
                connector_id=connector_id,
                policy_decision_id=policy_decision_id,
                fingerprint="c" * 64,
                lease_seconds=60,
                retention_hours=24,
            )
            return result.state, result.record.id

    claims = await asyncio.gather(claim(), claim())
    assert sorted(state for state, _ in claims) == ["IN_PROGRESS", "NEW"]
    invocation_id = next(identifier for state, identifier in claims if state == "NEW")

    terminal = {
        "status": "COMPLETED",
        "output": {"document_id": str(uuid4())},
        "error_code": None,
        "error_message": None,
        "capability_version_id": str(capability_version_id),
        "connector_id": str(connector_id),
    }
    async with sessions() as session, session.begin():
        await OperatorInvocationStore().complete(
            session,
            invocation_id,
            result=terminal,
            succeeded=True,
        )
    async with sessions() as session, session.begin():
        replay = await OperatorInvocationStore().claim(
            session,
            principal,
            request_id=request_id,
            capability_name="knowledge.ingest",
            capability_version_id=capability_version_id,
            connector_id=connector_id,
            policy_decision_id=policy_decision_id,
            fingerprint="c" * 64,
            lease_seconds=60,
            retention_hours=24,
        )
        assert replay.state == "REPLAY"
        assert replay.replayed_result == terminal

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            for statement in (
                update(OperatorCapabilityInvocation)
                .where(OperatorCapabilityInvocation.id == invocation_id)
                .values(result={"rewritten": True}),
                delete(OperatorCapabilityInvocation).where(
                    OperatorCapabilityInvocation.id == invocation_id
                ),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()
        finally:
            await transaction.rollback()

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE operator_capability_invocations DISABLE TRIGGER "
            "trg_operator_capability_invocations_guard"
        )
        await connection.execute(
            delete(OperatorCapabilityInvocation).where(
                OperatorCapabilityInvocation.organization_id == organization_id
            )
        )
        await connection.exec_driver_sql(
            "ALTER TABLE operator_capability_invocations ENABLE TRIGGER "
            "trg_operator_capability_invocations_guard"
        )
        await connection.exec_driver_sql(
            "ALTER TABLE policy_decisions DISABLE TRIGGER trg_policy_decisions_immutable"
        )
        await connection.execute(
            delete(PolicyDecision).where(PolicyDecision.organization_id == organization_id)
        )
        await connection.exec_driver_sql(
            "ALTER TABLE policy_decisions ENABLE TRIGGER trg_policy_decisions_immutable"
        )
        await connection.execute(delete(Organization).where(Organization.id == organization_id))
    await engine.dispose()
