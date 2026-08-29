from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from obsion.api.schemas import CreateTurnRequest, CreateWorkspaceRequest
from obsion.application.workspaces import WorkspaceService
from obsion.artifacts.store import InMemoryObjectStore
from obsion.bootstrap import bootstrap_development_identity
from obsion.capabilities.gateway import (
    CapabilityGateway,
    GatewayRequest,
    GatewayResult,
    GatewayStatus,
)
from obsion.config import Settings
from obsion.db.models import Event, Run, RunStep
from obsion.db.session import Database
from obsion.domain.enums import RunStatus, StepKind, StepStatus
from obsion.harness.runtime import HarnessRuntime
from obsion.model_gateway.gateway import ModelGateway
from obsion.security.auth import load_principal_by_id


class BlockingGateway:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.step_ids: list[UUID] = []

    async def invoke(self, _session: object, request: GatewayRequest) -> GatewayResult:
        assert request.step_id is not None
        self.step_ids.append(request.step_id)
        self.started.set()
        await self.release.wait()
        return GatewayResult(
            status=GatewayStatus.COMPLETED,
            policy_decision_id=uuid4(),
            evidence_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_cancelled_run_never_starts_a_dependent_step(app_settings: Settings) -> None:
    database = Database(app_settings)
    service = WorkspaceService(app_settings)
    gateway = BlockingGateway()
    execution: asyncio.Task[None] | None = None
    try:
        async with database.sessions() as session, session.begin():
            await bootstrap_development_identity(session, app_settings)
            principal = await load_principal_by_id(
                session,
                app_settings.dev_organization_id,
                app_settings.dev_user_id,
            )
            workspace = await service.create_workspace(
                session,
                principal,
                CreateWorkspaceRequest(name="Phase 4 cancellation"),
            )
            thread = await service.create_thread(
                session,
                principal,
                workspace.id,
                "Cancellation barrier",
            )
            _, run = await service.create_turn(
                session,
                principal,
                thread.id,
                CreateTurnRequest(input="Execute a two-step governed plan"),
            )
            run.status = RunStatus.RUNNING
            run.plan = {"route": "TEST", "steps": []}
            run.step_count = 2
            first = RunStep(
                organization_id=principal.organization_id,
                run_id=run.id,
                ordinal=3,
                name="Already running boundary",
                kind=StepKind.CAPABILITY,
                status=StepStatus.PENDING,
                depends_on=[],
                input_payload={
                    "capability": "test.first",
                    "payload": {},
                    "resource": {},
                    "environment": "development",
                },
            )
            second = RunStep(
                organization_id=principal.organization_id,
                run_id=run.id,
                ordinal=4,
                name="Must never start after cancellation",
                kind=StepKind.CAPABILITY,
                status=StepStatus.PENDING,
                depends_on=[3],
                input_payload={
                    "capability": "test.second",
                    "payload": {},
                    "resource": {},
                    "environment": "development",
                },
            )
            session.add_all([first, second])
            await session.flush()
            run_id = run.id
            first_id = first.id
            second_id = second.id

        runtime = HarnessRuntime(
            database,
            app_settings,
            cast(CapabilityGateway, gateway),
            ModelGateway(app_settings),
            InMemoryObjectStore(),
        )
        execution = asyncio.create_task(runtime.execute(app_settings.dev_organization_id, run_id))
        await asyncio.wait_for(gateway.started.wait(), timeout=2)

        async with database.sessions() as session, session.begin():
            principal = await load_principal_by_id(
                session,
                app_settings.dev_organization_id,
                app_settings.dev_user_id,
            )
            cancelled = await service.cancel_run(session, principal, run_id)
            assert cancelled.status == RunStatus.CANCELLED
            assert cancelled.cancellation_requested_at is not None

        gateway.release.set()
        await asyncio.wait_for(execution, timeout=2)

        async with database.sessions() as session:
            stored_run = await session.get(Run, run_id)
            steps = list(
                await session.scalars(
                    select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
                )
            )
            events = list(
                await session.scalars(
                    select(Event).where(Event.run_id == run_id).order_by(Event.run_sequence)
                )
            )

        assert stored_run is not None
        assert stored_run.status == RunStatus.CANCELLED
        assert gateway.step_ids == [first_id]
        assert {item.id: item.status for item in steps} == {
            first_id: StepStatus.CANCELLED,
            second_id: StepStatus.CANCELLED,
        }
        names = [item.name for item in events]
        assert names[-2:] == ["run.cancellation_requested", "run.cancelled"]
        assert "run.completed" not in names
        assert "answer.delta" not in names
    finally:
        gateway.release.set()
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
        await database.dispose()
