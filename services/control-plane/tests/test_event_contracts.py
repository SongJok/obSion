from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from obsion.api.schemas import EventView
from obsion.common.errors import ValidationError
from obsion.config import Settings
from obsion.contracts.events import (
    canonicalize_json,
    validate_event_contracts,
    validate_event_envelope,
)
from obsion.contracts.events.validation import prepare_event_draft, registered_event_versions
from obsion.db.models import AggregateHead, Event, OutboxMessage, Run
from obsion.db.session import Database
from obsion.domain.enums import ActorType, Classification
from obsion.persistence.events import EventDraft, EventStore

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"


class _ExampleEnum(StrEnum):
    VALUE = "VALUE"


def test_event_contract_registry_and_schemas_are_valid() -> None:
    summary = validate_event_contracts()
    assert summary.registry_version == 1
    assert summary.event_count == 92
    assert summary.version_count == 92


def test_event_contract_canonicalization_is_deterministic_and_rejects_unsafe_values() -> None:
    identifier = UUID("018f47ca-4a8c-7df5-9ad3-111111111111")
    observed_at = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
    assert canonicalize_json(
        {
            "identifier": identifier,
            "observed_at": observed_at,
            "amount": Decimal("12.3400"),
            "enum": _ExampleEnum.VALUE,
            "items": (1, True),
        }
    ) == {
        "identifier": str(identifier),
        "observed_at": "2026-08-26T12:30:00Z",
        "amount": "12.3400",
        "enum": "VALUE",
        "items": [1, True],
    }

    with pytest.raises(ValidationError) as unsafe:
        canonicalize_json({"created_at": datetime(2026, 8, 26, 12, 30)})
    assert unsafe.value.code == "event_payload_not_json_safe"
    assert unsafe.value.details["reason"] == "naive_datetime"


def test_notification_delivered_requires_exactly_one_delivery_target() -> None:
    notification_id = uuid4()

    assert _prepare_payload(
        "notification.delivered",
        {
            "notification_id": notification_id,
            "event": "action.completed",
        },
    ) == {
        "notification_id": str(notification_id),
        "event": "action.completed",
    }
    recipient_id = uuid4()
    assert _prepare_payload(
        "notification.delivered",
        {
            "notification_id": notification_id,
            "recipient_id": recipient_id,
        },
    ) == {
        "notification_id": str(notification_id),
        "recipient_id": str(recipient_id),
    }

    invalid_payloads: tuple[dict[str, object], ...] = (
        {"notification_id": notification_id},
        {
            "notification_id": notification_id,
            "event": "action.completed",
            "recipient_id": recipient_id,
        },
    )
    for invalid_payload in invalid_payloads:
        with pytest.raises(ValidationError) as invalid:
            _prepare_payload("notification.delivered", invalid_payload)
        assert invalid.value.code == "event_payload_schema_invalid"
        assert invalid.value.details["path"] == "$"
        assert invalid.value.details["validator"] == "oneOf"


def test_event_payload_error_code_must_be_registered() -> None:
    assert _prepare_payload(
        "run.failed",
        {"error_code": "run_timeout", "message": "deadline exceeded"},
    ) == {"error_code": "run_timeout", "message": "deadline exceeded"}

    with pytest.raises(ValidationError) as invalid:
        _prepare_payload(
            "run.failed",
            {"error_code": "definitely_unregistered_phase1_probe"},
        )
    assert invalid.value.code == "event_payload_schema_invalid"
    assert invalid.value.details["path"] == "$.error_code"


def test_event_envelope_requires_matching_id_and_event_id() -> None:
    event_id = uuid4()
    envelope = {
        "id": str(event_id),
        "event_id": str(uuid4()),
        "organization_id": str(uuid4()),
        "aggregate_type": "run",
        "aggregate_id": str(uuid4()),
        "sequence": 1,
        "name": "run.started",
        "run_id": None,
        "run_sequence": None,
        "causation_id": None,
        "correlation_id": str(uuid4()),
        "actor_type": "SYSTEM",
        "actor_id": None,
        "schema_version": 1,
        "classification": "INTERNAL",
        "payload": {"worker": "test-worker"},
        "created_at": "2026-08-26T12:30:00Z",
    }
    with pytest.raises(ValidationError) as mismatch:
        validate_event_envelope(envelope)
    assert mismatch.value.code == "event_envelope_schema_invalid"
    assert mismatch.value.details["path"] == "$.event_id"


def test_every_literal_production_event_draft_is_registered() -> None:
    registered_names = {name for name, _ in registered_event_versions()}
    literal_names: set[str] = set()
    for path in _SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node.func) != "EventDraft":
                continue
            event_name = next((item.value for item in node.keywords if item.arg == "name"), None)
            if isinstance(event_name, ast.Constant) and isinstance(event_name.value, str):
                literal_names.add(event_name.value)
    assert literal_names <= registered_names


async def test_invalid_event_is_rejected_without_sequence_or_outbox_side_effects(
    app_settings: Settings,
) -> None:
    database = Database(app_settings)
    store = EventStore()
    organization_id = uuid4()
    run_id = uuid4()
    aggregate_id = uuid4()
    await _insert_minimal_run(database, organization_id, run_id)

    try:
        async with database.sessions() as session:
            with pytest.raises(ValidationError) as unknown:
                await store.append(
                    session,
                    EventDraft(
                        name="unregistered.event",
                        aggregate_type="run",
                        aggregate_id=aggregate_id,
                        organization_id=organization_id,
                        correlation_id=run_id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        run_id=run_id,
                    ),
                )
            assert unknown.value.code == "event_name_unregistered"
            assert not session.new
            assert not session.dirty
            await session.rollback()

        await _assert_no_event_side_effects(database, organization_id, run_id, aggregate_id)

        async with database.sessions() as session:
            with pytest.raises(ValidationError) as invalid_payload:
                await store.append(
                    session,
                    EventDraft(
                        name="run.started",
                        aggregate_type="run",
                        aggregate_id=aggregate_id,
                        organization_id=organization_id,
                        correlation_id=run_id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        run_id=run_id,
                        payload={"worker": 42},
                    ),
                )
            assert invalid_payload.value.code == "event_payload_schema_invalid"
            assert invalid_payload.value.details["path"] == "$.worker"
            assert not session.new
            assert not session.dirty
            await session.rollback()

        await _assert_no_event_side_effects(database, organization_id, run_id, aggregate_id)

        async with database.sessions() as session:
            with pytest.raises(ValidationError) as unsupported_version:
                await store.append(
                    session,
                    EventDraft(
                        name="run.started",
                        aggregate_type="run",
                        aggregate_id=aggregate_id,
                        organization_id=organization_id,
                        correlation_id=run_id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        run_id=run_id,
                        schema_version=2,
                        payload={"worker": "test-worker"},
                    ),
                )
            assert unsupported_version.value.code == "event_schema_version_unsupported"
            assert unsupported_version.value.details["supported_versions"] == [1]
            assert not session.new
            assert not session.dirty
            await session.rollback()

        await _assert_no_event_side_effects(database, organization_id, run_id, aggregate_id)
    finally:
        await database.dispose()


async def test_final_envelope_failure_does_not_mutate_event_sequences(
    app_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(app_settings)
    store = EventStore()
    organization_id = uuid4()
    run_id = uuid4()
    await _insert_minimal_run(database, organization_id, run_id)

    def reject_final_envelope(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ValidationError(
            "event_envelope_schema_invalid",
            "Injected final envelope failure",
        )

    monkeypatch.setattr(
        "obsion.persistence.events.validate_event_envelope",
        reject_final_envelope,
    )
    try:
        async with database.sessions() as session, session.begin():
            with pytest.raises(ValidationError) as rejected:
                await store.append(
                    session,
                    EventDraft(
                        name="run.started",
                        aggregate_type="run",
                        aggregate_id=run_id,
                        organization_id=organization_id,
                        correlation_id=run_id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        run_id=run_id,
                        payload={"worker": "test-worker"},
                    ),
                )
            assert rejected.value.code == "event_envelope_schema_invalid"
            assert not session.new
            assert not session.dirty

        await _assert_no_event_side_effects(database, organization_id, run_id, run_id)
    finally:
        await database.dispose()


async def test_valid_event_uses_one_frozen_envelope_for_event_api_and_outbox(
    app_settings: Settings,
) -> None:
    database = Database(app_settings)
    store = EventStore()
    organization_id = uuid4()
    run_id = uuid4()
    await _insert_minimal_run(database, organization_id, run_id)

    try:
        async with database.sessions() as session, session.begin():
            event = await store.append(
                session,
                EventDraft(
                    name="approval.requested",
                    aggregate_type="run",
                    aggregate_id=run_id,
                    organization_id=organization_id,
                    correlation_id=run_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    run_id=run_id,
                    classification=Classification.CONFIDENTIAL,
                    payload={
                        "approval_id": uuid4(),
                        "expires_at": datetime(2026, 8, 26, 13, 30, tzinfo=UTC),
                    },
                ),
            )
            outbox = await session.scalar(
                select(OutboxMessage).where(OutboxMessage.event_id == event.id)
            )
            assert outbox is not None
            assert outbox.payload["id"] == str(event.id)
            assert outbox.payload["event_id"] == str(event.id)
            assert outbox.payload["organization_id"] == str(organization_id)
            assert outbox.payload["aggregate_type"] == "run"
            assert outbox.payload["aggregate_id"] == str(run_id)
            assert outbox.payload["run_id"] == str(run_id)
            assert outbox.payload["run_sequence"] == 1
            assert outbox.payload["actor_type"] == "SYSTEM"
            assert outbox.payload["schema_version"] == 1
            assert outbox.payload["classification"] == "CONFIDENTIAL"
            assert outbox.payload["payload"] == event.payload
            event_view = EventView.model_validate(event).model_dump(mode="json")
            assert event_view == {key: outbox.payload[key] for key in EventView.model_fields}
            assert event.payload["expires_at"] == "2026-08-26T13:30:00Z"
    finally:
        await database.dispose()


def _prepare_payload(name: str, payload: dict[str, object]) -> dict[str, object]:
    identifier = uuid4()
    return prepare_event_draft(
        event_id=identifier,
        name=name,
        schema_version=1,
        organization_id=uuid4(),
        aggregate_type="run",
        aggregate_id=identifier,
        run_id=identifier,
        causation_id=None,
        correlation_id=identifier,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        classification=Classification.INTERNAL,
        payload=payload,
        created_at=datetime(2026, 8, 26, 12, 30, tzinfo=UTC),
    ).payload


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


async def _insert_minimal_run(database: Database, organization_id: UUID, run_id: UUID) -> None:
    # SQLite tests intentionally disable FK checks; the Event atomicity contract only
    # requires a lockable Run row and does not depend on unrelated lifecycle fixtures.
    async with database.sessions() as session, session.begin():
        session.add(
            Run(
                id=run_id,
                organization_id=organization_id,
                turn_id=uuid4(),
                aggregate_version=0,
            )
        )


async def _assert_no_event_side_effects(
    database: Database,
    organization_id: UUID,
    run_id: UUID,
    aggregate_id: UUID,
) -> None:
    async with database.sessions() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.aggregate_version == 0
        assert (
            await session.scalar(
                select(AggregateHead).where(
                    AggregateHead.organization_id == organization_id,
                    AggregateHead.aggregate_type == "run",
                    AggregateHead.aggregate_id == aggregate_id,
                )
            )
            is None
        )
        assert await session.scalar(select(func.count()).select_from(Event)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 0
