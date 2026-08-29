import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from obsion.config import get_settings
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelProfile,
    Organization,
    User,
)


@pytest.mark.asyncio
async def test_completed_evaluation_and_case_results_are_immutable() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    agent_id = uuid4()
    agent_version_id = uuid4()
    profile_id = uuid4()
    dataset_id = uuid4()
    case_id = uuid4()
    run_id = uuid4()
    result_id = uuid4()
    now = datetime.now(UTC)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"evaluation-invariant-{organization_id}",
                    name="Evaluation invariant",
                    active=True,
                    settings={},
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="evaluation-invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Evaluation invariant owner",
                    active=True,
                    attributes={},
                )
            )
            await connection.execute(
                insert(AgentDefinition).values(
                    id=agent_id,
                    organization_id=organization_id,
                    name="evaluation-agent",
                    display_name="Evaluation agent",
                    description="",
                    status="ACTIVE",
                )
            )
            await connection.execute(
                insert(AgentVersion).values(
                    id=agent_version_id,
                    organization_id=organization_id,
                    agent_id=agent_id,
                    version=1,
                    spec={"capabilities": []},
                    checksum_sha256="a" * 64,
                    created_by=user_id,
                    created_at=now,
                )
            )
            await connection.execute(
                insert(ModelProfile).values(
                    id=profile_id,
                    organization_id=organization_id,
                    name="evaluation-profile",
                    requirements={},
                    routing_policy={},
                    enabled=True,
                )
            )
            await connection.execute(
                insert(EvaluationDataset).values(
                    id=dataset_id,
                    organization_id=organization_id,
                    name="evaluation-invariant",
                    description="",
                    domain="foundation",
                )
            )
            await connection.execute(
                insert(EvaluationCase).values(
                    id=case_id,
                    organization_id=organization_id,
                    dataset_id=dataset_id,
                    external_id="route-001",
                    version=1,
                    evaluator="ROUTING",
                    input_payload={"question": "What is the policy?"},
                    expected={"route": "KNOWLEDGE"},
                    fixtures={},
                    created_at=now,
                )
            )
            await connection.execute(
                insert(EvaluationRun).values(
                    id=run_id,
                    organization_id=organization_id,
                    dataset_id=dataset_id,
                    agent_version_id=agent_version_id,
                    model_profile_id=profile_id,
                    application_revision="invariant-test",
                    status="RUNNING",
                    requested_by=user_id,
                    dataset_snapshot_sha256="b" * 64,
                    snapshot_sha256="c" * 64,
                    configuration_snapshot={},
                    metrics={},
                    started_at=now,
                )
            )
            await connection.execute(
                insert(EvaluationCaseResult).values(
                    id=result_id,
                    organization_id=organization_id,
                    evaluation_run_id=run_id,
                    evaluation_case_id=case_id,
                    ordinal=1,
                    external_id="route-001",
                    case_version=1,
                    evaluator="ROUTING",
                    status="PASSED",
                    case_snapshot_sha256="d" * 64,
                    checks={"route": True},
                    scores={"route_accuracy": 1.0},
                    observed={"route": "KNOWLEDGE"},
                    evidence_refs=[],
                    duration_ms=1,
                    created_at=now,
                )
            )
            await connection.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id)
                .values(
                    status="COMPLETED",
                    gate_passed=True,
                    metrics={"pass_rate": 1.0},
                    completed_at=now,
                )
            )

            result_mutation = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(EvaluationCaseResult)
                    .where(EvaluationCaseResult.id == result_id)
                    .values(scores={"route_accuracy": 0.0})
                )
            await result_mutation.rollback()

            run_mutation = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(EvaluationRun)
                    .where(EvaluationRun.id == run_id)
                    .values(gate_passed=False)
                )
            await run_mutation.rollback()

            result_removal = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    delete(EvaluationCaseResult).where(EvaluationCaseResult.id == result_id)
                )
            await result_removal.rollback()

            persisted = await connection.scalar(
                select(EvaluationCaseResult.scores).where(EvaluationCaseResult.id == result_id)
            )
            assert persisted == {"route_accuracy": 1.0}
        finally:
            await transaction.rollback()
    await engine.dispose()
