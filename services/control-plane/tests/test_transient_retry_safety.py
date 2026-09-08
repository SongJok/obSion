"""Database-backed retry admission and dependency recovery (no mocked ORM).

SQLite runs by default. Set OBSION_RUN_POSTGRES_TESTS=1 and an explicit
OBSION_DATABASE_URL to repeat against a migrated disposable PostgreSQL database.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from obsion.common.errors import NotFoundError
from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import (
    CapabilityDefinition,
    CapabilityVersion,
    Event,
    Organization,
    Run,
    RunStep,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    CapabilityTransport,
    RiskLevel,
    RunStatus,
    SideEffect,
    StepKind,
    StepStatus,
)
from obsion.harness.runtime import HarnessRuntime
from obsion.harness.steps import StepExecutor
from obsion.persistence.events import EventStore


@dataclass
class RetryCase:
    database: Database
    runtime: HarnessRuntime
    organization_id: UUID
    run_id: UUID
    pins: dict[str, UUID | None]


@pytest.fixture
async def retry_case(tmp_path: Path) -> AsyncIterator[RetryCase]:
    postgres = os.getenv("OBSION_RUN_POSTGRES_TESTS") == "1"
    url = (
        os.environ["OBSION_DATABASE_URL"]
        if postgres
        else f"sqlite+aiosqlite:///{tmp_path / 'transient-retry.db'}"
    )
    settings = Settings(_env_file=None, environment=Environment.TEST, database_url=url)
    database = Database(settings)
    organization_id, foreign_id = uuid4(), uuid4()
    try:
        if not postgres:
            async with database.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            session.add_all(
                Organization(id=value, slug=f"retry-{value}", name="Retry safety")
                for value in (organization_id, foreign_id)
            )
            await session.flush()
            user = User(
                organization_id=organization_id,
                external_id="operator",
                email="operator@example.test",
                display_name="Operator",
            )
            session.add(user)
            await session.flush()
            workspace = Workspace(
                organization_id=organization_id, name="Retry safety", owner_id=user.id
            )
            session.add(workspace)
            await session.flush()
            thread = Thread(
                organization_id=organization_id,
                workspace_id=workspace.id,
                title="Retry safety",
                created_by=user.id,
            )
            session.add(thread)
            await session.flush()
            turn = Turn(
                organization_id=organization_id,
                thread_id=thread.id,
                ordinal=1,
                created_by=user.id,
                input_text="Read governed data",
                sanitized_input="Read governed data",
                created_at=utc_now(),
            )
            session.add(turn)
            await session.flush()
            run = Run(
                organization_id=organization_id,
                turn_id=turn.id,
                status=RunStatus.RUNNING,
                plan={"route": "INCIDENT", "steps": []},
                step_count=3,
            )
            local = CapabilityDefinition(
                organization_id=organization_id, name="retry.safety", display_name="Retry safety"
            )
            foreign = CapabilityDefinition(
                organization_id=foreign_id, name="retry.safety", display_name="Foreign safety"
            )
            session.add_all([run, local, foreign])
            await session.flush()
            pins: dict[str, UUID | None] = {"unpinned": None, "missing": uuid4()}
            for version_number, effect in enumerate(SideEffect, start=1):
                version = _version(organization_id, local.id, version_number, effect)
                session.add(version)
                pins[effect.value] = version.id
            foreign_version = _version(foreign_id, foreign.id, 1, SideEffect.NONE)
            session.add(foreign_version)
            pins["foreign"] = foreign_version.id
            run_id = run.id
        runtime = object.__new__(HarnessRuntime)
        runtime.database = database
        runtime.events = EventStore()
        yield RetryCase(database, runtime, organization_id, run_id, pins)
    finally:
        await database.dispose()


def _version(
    organization_id: UUID, capability_id: UUID, number: int, effect: SideEffect
) -> CapabilityVersion:
    return CapabilityVersion(
        id=uuid4(),
        organization_id=organization_id,
        capability_id=capability_id,
        version=number,
        transport=CapabilityTransport.INTERNAL,
        risk_level=RiskLevel.L1,
        side_effect=effect,
        permission_action="retry.safety",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        checksum_sha256="a" * 64,
        created_at=utc_now(),
    )


def _step(
    case: RetryCase,
    ordinal: int,
    *,
    pin: str = "NONE",
    status: StepStatus = StepStatus.FAILED,
    error_code: str | None = "capability_timeout",
    depends_on: tuple[int, ...] = (),
    kind: StepKind = StepKind.CAPABILITY,
    retry_count: int = 0,
    max_retries: int = 1,
) -> RunStep:
    return RunStep(
        organization_id=case.organization_id,
        run_id=case.run_id,
        ordinal=ordinal,
        name=f"Step {ordinal}",
        kind=kind,
        status=status,
        capability_version_id=case.pins[pin],
        input_payload={"capability": "retry.safety", "payload": {}, "environment": "test"},
        depends_on=list(depends_on),
        error_code=error_code,
        retry_count=retry_count,
        max_retries=max_retries,
        started_at=utc_now() if status != StepStatus.SKIPPED else None,
        completed_at=utc_now(),
        output_ref="previous-output" if status != StepStatus.SKIPPED else None,
    )


async def _persist(case: RetryCase, *steps: RunStep) -> None:
    async with case.database.sessions() as session, session.begin():
        session.add_all(steps)


async def _read(case: RetryCase) -> tuple[Run, dict[int, RunStep], list[Event]]:
    # A fresh session verifies committed state rather than an ORM identity-map mutation.
    async with case.database.sessions() as session:
        run = await session.get(Run, case.run_id)
        assert run is not None
        steps = list(await session.scalars(select(RunStep).where(RunStep.run_id == case.run_id)))
        events = list(
            await session.scalars(
                select(Event).where(Event.run_id == case.run_id).order_by(Event.run_sequence)
            )
        )
        return run, {step.ordinal: step for step in steps}, events


@pytest.mark.parametrize(
    "error_code", ["capability_failed", "capability_timeout", "rate_limit_unavailable"]
)
async def test_pinned_read_only_failure_retries_and_persists_events(
    retry_case: RetryCase, error_code: str
) -> None:
    case = retry_case
    await _persist(case, _step(case, 1, error_code=error_code))
    assert await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    run, steps, events = await _read(case)
    step = steps[1]
    assert step.status == StepStatus.PENDING
    assert step.retry_count == 1
    assert step.capability_version_id == case.pins["NONE"]
    assert step.started_at is step.completed_at is step.error_code is step.output_ref is None
    assert run.status == RunStatus.RUNNING
    assert run.step_count == 3
    assert run.plan["replans"] == [
        {"attempt": 1, "reason": "transient_read_only_failure", "step_ordinals": [1]}
    ]
    assert [event.name for event in events] == [
        "run.state_changed",
        "plan.updated",
        "run.state_changed",
    ]
    assert [event.run_sequence for event in events] == [1, 2, 3]
    assert events[1].payload["replan"] == run.plan["replans"][0]
    assert not await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    assert len((await _read(case))[2]) == 3


@pytest.mark.parametrize(
    "pin", ["IDEMPOTENT_WRITE", "WRITE", "DESTRUCTIVE", "unpinned", "missing", "foreign"]
)
@pytest.mark.parametrize(
    "error_code", ["capability_failed", "capability_timeout", "rate_limit_unavailable"]
)
async def test_unsafe_or_unknown_pin_fails_closed_without_mutation(
    retry_case: RetryCase, pin: str, error_code: str
) -> None:
    case = retry_case
    await _persist(case, _step(case, 1, pin=pin, error_code=error_code))
    _, before, _ = await _read(case)
    assert not await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    run, steps, events = await _read(case)
    assert run.status == RunStatus.RUNNING
    assert "replans" not in run.plan
    assert not events
    for field in (
        "status",
        "retry_count",
        "capability_version_id",
        "error_code",
        "output_ref",
        "started_at",
        "completed_at",
        "updated_at",
    ):
        assert getattr(steps[1], field) == getattr(before[1], field)


async def test_write_pin_does_not_inherit_latest_read_only_metadata(retry_case: RetryCase) -> None:
    case = retry_case
    async with case.database.sessions() as session, session.begin():
        pinned = await session.get(CapabilityVersion, case.pins["WRITE"])
        assert pinned is not None
        session.add(_version(case.organization_id, pinned.capability_id, 5, SideEffect.NONE))
    await _persist(case, _step(case, 1, pin="WRITE"))
    assert not await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    assert (await _read(case))[1][1].status == StepStatus.FAILED


@pytest.mark.parametrize("max_retries", [0, 1, 2])
async def test_retry_budget_is_consumed_only_on_failed_attempts(
    retry_case: RetryCase, max_retries: int
) -> None:
    case = retry_case
    await _persist(case, _step(case, 1, max_retries=max_retries))
    for attempt in range(1, max_retries + 1):
        assert await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
        run, steps, events = await _read(case)
        assert steps[1].retry_count == attempt
        assert steps[1].status == StepStatus.PENDING
        assert len(events) == attempt * 3
        assert run.plan["replans"][-1]["attempt"] == attempt
        assert not await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
        async with case.database.sessions() as session, session.begin():
            step = await session.get(RunStep, steps[1].id)
            assert step is not None
            step.status = StepStatus.FAILED
            step.error_code = "capability_timeout"
            step.started_at = step.completed_at = utc_now()
    assert not await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    run, steps, events = await _read(case)
    assert steps[1].status == StepStatus.FAILED
    assert steps[1].retry_count == max_retries
    assert steps[1].error_code == "capability_timeout"
    assert len(run.plan.get("replans", [])) == max_retries
    assert len(events) == 3 * max_retries


@pytest.mark.parametrize(
    ("status", "error_code", "kind"),
    [
        (StepStatus.FAILED, "capability_denied", StepKind.CAPABILITY),
        (StepStatus.FAILED, "capability_timeout", StepKind.MODEL),
        (StepStatus.COMPLETED, "capability_timeout", StepKind.CAPABILITY),
        (StepStatus.CANCELLED, "capability_timeout", StepKind.CAPABILITY),
    ],
)
async def test_only_transient_failed_capability_steps_are_retry_candidates(
    retry_case: RetryCase, status: StepStatus, error_code: str, kind: StepKind
) -> None:
    case = retry_case
    await _persist(case, _step(case, 1, status=status, error_code=error_code, kind=kind))
    assert not await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    assert not (await _read(case))[2]


async def test_dependency_skips_restore_transitively_including_core_steps(
    retry_case: RetryCase,
) -> None:
    case = retry_case
    await _persist(
        case,
        _step(case, 1, status=StepStatus.COMPLETED, error_code=None),
        _step(case, 2, depends_on=(1,)),
        # An unexecuted write is not a retry: it remains subject to the normal gateway.
        _step(
            case,
            3,
            pin="WRITE",
            status=StepStatus.SKIPPED,
            error_code="dependency_failed",
            depends_on=(1, 2),
            max_retries=0,
        ),
        _step(
            case,
            4,
            pin="unpinned",
            kind=StepKind.VERIFY,
            status=StepStatus.SKIPPED,
            error_code="dependency_failed",
            depends_on=(2, 3),
            max_retries=0,
        ),
        _step(
            case,
            5,
            pin="unpinned",
            kind=StepKind.REFLECT,
            status=StepStatus.SKIPPED,
            error_code="dependency_failed",
            depends_on=(4,),
            max_retries=0,
        ),
        _step(
            case,
            6,
            pin="unpinned",
            kind=StepKind.RESPOND,
            status=StepStatus.SKIPPED,
            error_code="dependency_failed",
            depends_on=(5,),
            max_retries=0,
        ),
    )
    assert await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    run, steps, _ = await _read(case)
    assert run.plan["replans"][0]["step_ordinals"] == [2, 3, 4, 5, 6]
    assert steps[1].status == StepStatus.COMPLETED
    assert steps[2].retry_count == 1
    assert all(steps[ordinal].retry_count == 0 for ordinal in (3, 4, 5, 6))
    assert all(steps[ordinal].status == StepStatus.PENDING for ordinal in (2, 3, 4, 5, 6))
    wave = StepExecutor().next_wave(list(steps.values()))
    assert [step.ordinal for step in wave.ready] == [2]
    assert not wave.blocked
    assert not wave.deadlocked
    async with case.database.sessions() as session, session.begin():
        step = await session.get(RunStep, steps[2].id)
        assert step is not None
        step.status = StepStatus.COMPLETED
    _, steps, _ = await _read(case)
    assert [step.ordinal for step in StepExecutor().next_wave(list(steps.values())).ready] == [3]


async def test_skips_with_unrecovered_dependencies_or_other_reasons_remain_skipped(
    retry_case: RetryCase,
) -> None:
    case = retry_case
    await _persist(
        case,
        _step(case, 1),
        _step(case, 2, pin="WRITE"),
        _step(
            case, 3, status=StepStatus.SKIPPED, error_code="dependency_failed", depends_on=(1, 2)
        ),
        _step(case, 4, status=StepStatus.SKIPPED, error_code="dependency_failed", depends_on=(3,)),
        _step(case, 5, status=StepStatus.SKIPPED, error_code="capability_denied", depends_on=(1,)),
        _step(
            case, 6, status=StepStatus.SKIPPED, error_code="dependency_failed", depends_on=(1, 99)
        ),
        _step(case, 7, status=StepStatus.CANCELLED),
        _step(
            case, 8, status=StepStatus.SKIPPED, error_code="dependency_failed", depends_on=(1, 7)
        ),
        _step(case, 9, pin="missing"),
        _step(
            case, 10, status=StepStatus.SKIPPED, error_code="dependency_failed", depends_on=(1, 9)
        ),
    )
    _, before, _ = await _read(case)
    assert await case.runtime._replan_transient_failures(case.organization_id, case.run_id)
    run, steps, _ = await _read(case)
    assert run.plan["replans"][0]["step_ordinals"] == [1]
    for ordinal in range(2, 11):
        assert steps[ordinal].status == before[ordinal].status
        assert steps[ordinal].error_code == before[ordinal].error_code
        assert steps[ordinal].retry_count == 0
        assert steps[ordinal].completed_at == before[ordinal].completed_at


async def test_foreign_run_lookup_is_rejected_without_events(retry_case: RetryCase) -> None:
    case = retry_case
    await _persist(case, _step(case, 1))
    with pytest.raises(NotFoundError):
        await case.runtime._replan_transient_failures(uuid4(), case.run_id)
    assert not (await _read(case))[2]
