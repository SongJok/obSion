from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import configure_mappers

from obsion.actions.gateway import ActionGatewayResult, ActionGatewayStatus
from obsion.api.schemas import ErrorBody
from obsion.capabilities.gateway import GatewayResult, GatewayStatus
from obsion.contracts.errors import ErrorContractDefinitionError
from obsion.db import models as db_models
from obsion.db.base import Base
from obsion.db.types import ErrorCodeType
from obsion.domain.enums import EvaluationResultStatus
from obsion.evaluations.engine import CaseEvaluation

_ERROR_CODE_FIELD_NAMES = frozenset({"error_code", "last_error_code"})
_REGISTERED_CODE = "internal_error"
_UNREGISTERED_CODE = "unregistered_runtime_error"

_RESULT_FACTORIES: tuple[Callable[[str | None], object], ...] = (
    lambda code: GatewayResult(
        status=GatewayStatus.FAILED,
        policy_decision_id=uuid4(),
        error_code=code,
    ),
    lambda code: ActionGatewayResult(
        status=ActionGatewayStatus.FAILED,
        policy_decision_id=uuid4(),
        error_code=code,
    ),
    lambda code: CaseEvaluation(
        status=EvaluationResultStatus.ERROR,
        checks={},
        scores={},
        observed={},
        evidence_refs=[],
        duration_ms=0,
        error_code=code,
    ),
)

_PERSISTED_FIELD_LENGTHS: dict[tuple[str, str], int] = {
    ("action_attempts", "error_code"): 100,
    ("action_requests", "error_code"): 100,
    ("automation_executions", "error_code"): 100,
    ("automation_step_executions", "error_code"): 100,
    ("evaluation_case_results", "error_code"): 160,
    ("operator_capability_invocations", "error_code"): 160,
    ("run_steps", "error_code"): 100,
    ("runs", "error_code"): 100,
    ("verification_assessments", "error_code"): 120,
    ("workflow_schedules", "last_error_code"): 100,
}


def _persisted_columns() -> dict[tuple[str, str], Column[Any]]:
    # Importing the model module registers every mapped table with the shared metadata.
    assert db_models.Run.__table__ in Base.metadata.tables.values()
    configure_mappers()
    return {
        (table.fullname, column.name): column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name in _ERROR_CODE_FIELD_NAMES
    }


def _persisted_fields() -> tuple[tuple[type[Base], str], ...]:
    columns = _persisted_columns()
    discovered: dict[tuple[str, str], tuple[type[Base], str]] = {}
    for mapper in Base.registry.mappers:
        for attribute in mapper.column_attrs:
            if len(attribute.columns) != 1:
                continue
            column = attribute.columns[0]
            assert isinstance(column, Column)
            assert column.table is not None
            key = (column.table.fullname, column.name)
            if key not in columns:
                continue
            assert key not in discovered
            discovered[key] = (mapper.class_, attribute.key)
    assert set(discovered) == set(columns)
    return tuple(discovered[key] for key in sorted(discovered))


_PERSISTED_FIELDS = _persisted_fields()


@pytest.mark.parametrize("factory", _RESULT_FACTORIES)
def test_result_error_codes_are_validated_at_construction(
    factory: Callable[[str | None], object],
) -> None:
    factory(None)
    factory(_REGISTERED_CODE)

    with pytest.raises(
        ErrorContractDefinitionError,
        match="Unregistered Obsion error code",
    ):
        factory(_UNREGISTERED_CODE)


def test_error_body_code_is_validated_at_construction() -> None:
    body = ErrorBody(
        code=_REGISTERED_CODE,
        message="safe",
        correlation_id="request-id",
        details={},
    )
    assert body.code == _REGISTERED_CODE

    with pytest.raises(
        ErrorContractDefinitionError,
        match="Unregistered Obsion error code",
    ):
        ErrorBody(
            code=_UNREGISTERED_CODE,
            message="safe",
            correlation_id="request-id",
            details={},
        )


def test_all_persisted_error_code_columns_use_the_central_type_and_frozen_widths() -> None:
    columns = _persisted_columns()
    assert columns
    assert set(columns) == set(_PERSISTED_FIELD_LENGTHS)

    for key, column in columns.items():
        error_code_type = column.type
        assert isinstance(error_code_type, ErrorCodeType)
        assert error_code_type.column_length == _PERSISTED_FIELD_LENGTHS[key]


@pytest.mark.parametrize(("model", "field"), _PERSISTED_FIELDS)
def test_persisted_error_codes_are_validated_on_constructor_and_assignment(
    model: type[Base],
    field: str,
) -> None:
    instance = model(**{field: None})
    setattr(instance, field, _REGISTERED_CODE)
    assert getattr(instance, field) == _REGISTERED_CODE

    with pytest.raises(
        ErrorContractDefinitionError,
        match="Unregistered Obsion error code",
    ):
        model(**{field: _UNREGISTERED_CODE})

    with pytest.raises(
        ErrorContractDefinitionError,
        match="Unregistered Obsion error code",
    ):
        setattr(instance, field, _UNREGISTERED_CODE)


def test_error_code_database_type_fails_closed_on_bind_and_load() -> None:
    metadata = MetaData()
    records = Table(
        "runtime_error_code_records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("error_code", ErrorCodeType(100)),
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    try:
        with engine.begin() as connection:
            connection.execute(records.insert().values(id=1, error_code=_REGISTERED_CODE))
            connection.execute(records.insert().values(id=2, error_code=None))

        with engine.connect() as connection:
            values = connection.execute(
                select(records.c.error_code).order_by(records.c.id)
            ).scalars()
            assert list(values) == [_REGISTERED_CODE, None]

        with (
            pytest.raises(StatementError, match="Unregistered Obsion error code"),
            engine.begin() as connection,
        ):
            connection.execute(records.insert().values(id=3, error_code=_UNREGISTERED_CODE))

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO runtime_error_code_records (id, error_code) VALUES (?, ?)",
                (4, _UNREGISTERED_CODE),
            )

        with (
            pytest.raises(
                ErrorContractDefinitionError,
                match="Unregistered Obsion error code",
            ),
            engine.connect() as connection,
        ):
            connection.execute(select(records.c.error_code).where(records.c.id == 4)).scalar_one()
    finally:
        engine.dispose()
