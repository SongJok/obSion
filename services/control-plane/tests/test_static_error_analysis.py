from __future__ import annotations

import pytest
from static_contract_analysis import StaticContractAnalysisError
from static_error_analysis import analyze_error_producers

_CANONICAL_SOURCES = {
    "common/errors.py": """
class ObsionError(Exception):
    pass

class ConflictError(ObsionError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)

class AuthorizationError(ObsionError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)

class ValidationError(ObsionError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)

class NotFoundError(ObsionError):
    def __init__(self, resource: str, resource_id: object):
        super().__init__("resource_not_found", resource)

class BudgetExceededError(ObsionError):
    def __init__(self, budget: str, limit: object):
        super().__init__("budget_exceeded", budget)
""",
    "model_gateway/gateway.py": """
from obsion.common.errors import ObsionError

class ModelUnavailableError(ObsionError):
    def __init__(self, message: str = "unavailable"):
        super().__init__("model_unavailable", message)
""",
    "api/schemas.py": """
class ErrorBody:
    pass
""",
    "capabilities/gateway.py": """
class GatewayResult:
    pass
""",
    "actions/gateway.py": """
class ActionGatewayResult:
    pass
""",
    "evaluations/engine.py": """
class CaseEvaluation:
    pass
""",
    "db/types.py": """
class ErrorCodeType:
    pass
""",
    "db/models.py": """
from sqlalchemy.orm import Mapped, mapped_column
from obsion.db.types import ErrorCodeType

class Probe:
    error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))

class Schedule:
    last_error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
""",
}
_IMPLICIT_CODES = frozenset(
    {
        "budget_exceeded",
        "model_unavailable",
        "resource_not_found",
    }
)


def _sources(producer: str, **extra_sources: str) -> dict[str, str]:
    return {
        **_CANONICAL_SOURCES,
        "producer.py": producer,
        **extra_sources,
    }


def _catalog(*codes: str) -> frozenset[str]:
    return _IMPLICIT_CODES | frozenset(codes)


def test_analyzer_only_counts_codes_that_reach_typed_origin_sinks() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
_RETRYABLE = {"consumer_only"}

def inspect(exc):
    if exc.code == "consumer_only" or exc.code in _RETRYABLE:
        return "consumer_only"
    return None
"""
        ),
        catalog_codes=_catalog("consumer_only"),
    )

    assert analysis.origin_sinks == {}
    assert analysis.forwarding_sinks == {}
    assert analysis.helper_caller_codes == {}
    assert analysis.active_origin_codes == set()


def test_analyzer_resolves_literals_conditionals_helpers_and_fstrings() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ConflictError, ValidationError

class Service:
    def direct(self, flag: bool):
        raise ValidationError("input_invalid" if flag else "output_invalid", "invalid")

    def branch(self, flag: bool):
        if flag:
            code = "dependency_failed"
        else:
            code = "internal_error"
        raise ConflictError(code, "failed")

    def task(self):
        self._check_version("workspace_task")

    def decision(self):
        self._check_version("workspace_decision")

    def _check_version(self, aggregate: str):
        raise ConflictError(f"{aggregate}_version_conflict", "stale")
"""
        ),
        catalog_codes=_catalog(
            "dependency_failed",
            "input_invalid",
            "internal_error",
            "output_invalid",
            "workspace_decision_version_conflict",
            "workspace_task_version_conflict",
        ),
    )

    assert analysis.origin_sinks == {
        "producer.py::Service.direct#ValidationError[1]": frozenset(
            {"input_invalid", "output_invalid"}
        ),
        "producer.py::Service.branch#ConflictError[1]": frozenset(
            {"dependency_failed", "internal_error"}
        ),
        "producer.py::Service._check_version#ConflictError[1]": frozenset(
            {
                "workspace_decision_version_conflict",
                "workspace_task_version_conflict",
            }
        ),
    }
    assert analysis.helper_caller_codes == {}


def test_analyzer_records_helper_callers_for_code_parameters_and_defaults() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ValidationError

class Service:
    def first(self):
        self._fail("first_failed")

    def second(self):
        self._fail(code="second_failed")

    def defaulted(self):
        self._fail()

    def _fail(self, code: str = "default_failed"):
        raise ValidationError(code, "failed")
"""
        ),
        catalog_codes=_catalog("default_failed", "first_failed", "second_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::Service._fail#ValidationError[1]": frozenset(
            {"default_failed", "first_failed", "second_failed"}
        )
    }
    assert analysis.helper_caller_codes == {
        "producer.py::Service.defaulted#_fail[1]": frozenset({"default_failed"}),
        "producer.py::Service.first#_fail[1]": frozenset({"first_failed"}),
        "producer.py::Service.second#_fail[1]": frozenset({"second_failed"}),
    }


def test_analyzer_resolves_implicit_fixed_code_error_subclasses() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import BudgetExceededError, NotFoundError
from obsion.model_gateway.gateway import ModelUnavailableError

def produce():
    NotFoundError("run", "run-1")
    BudgetExceededError("tokens", 10)
    ModelUnavailableError()
"""
        ),
        catalog_codes=_catalog(),
    )

    assert analysis.origin_sinks == {
        "producer.py::produce#BudgetExceededError[1]": frozenset({"budget_exceeded"}),
        "producer.py::produce#ModelUnavailableError[1]": frozenset({"model_unavailable"}),
        "producer.py::produce#NotFoundError[1]": frozenset({"resource_not_found"}),
    }


def test_analyzer_rejects_noncanonical_error_code_type_columns() -> None:
    fixtures: tuple[tuple[str, dict[str, str]], ...] = (
        (
            "from obsion.extra_models import ExternalProbe\n"
            "def produce():\n"
            "    return ExternalProbe(error_code='not_registered')",
            {
                "extra_models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "from obsion.db.types import ErrorCodeType\n"
                    "class ExternalProbe:\n"
                    "    error_code: Mapped[str | None] = mapped_column(\n"
                    "        ErrorCodeType(100)\n"
                    "    )"
                )
            },
        ),
        (
            "from obsion.extra_models import ExternalProbe\n"
            "def produce():\n"
            "    return ExternalProbe(code='not_registered')",
            {
                "extra_models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "from obsion.db.types import ErrorCodeType\n"
                    "class ExternalProbe:\n"
                    "    code: Mapped[str | None] = mapped_column(\n"
                    "        ErrorCodeType(100)\n"
                    "    )"
                )
            },
        ),
        (
            "def produce():\n    return None",
            {
                "db/models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "from obsion.db.types import ErrorCodeType\n"
                    "class CustomProbe:\n"
                    "    code: Mapped[str | None] = mapped_column(\n"
                    "        ErrorCodeType(100)\n"
                    "    )"
                )
            },
        ),
    )
    for producer, extra_sources in fixtures:
        with pytest.raises(
            StaticContractAnalysisError,
            match="ErrorCodeType.*canonical|canonical.*ErrorCodeType|reviewed ORM Error field",
        ):
            analyze_error_producers(
                _sources(producer, **extra_sources),
                catalog_codes=_catalog(),
            )


def test_analyzer_resolves_error_code_type_aliases_and_reexports() -> None:
    fixtures: tuple[tuple[str, dict[str, str]], ...] = (
        (
            "from obsion.extra_models import ExternalProbe\n"
            "def produce():\n"
            "    return ExternalProbe(error_code='not_registered')",
            {
                "extra_models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "from obsion.db.types import ErrorCodeType as StoredCode\n"
                    "class ExternalProbe:\n"
                    "    error_code: Mapped[str | None] = mapped_column(\n"
                    "        StoredCode(100)\n"
                    "    )"
                )
            },
        ),
        (
            "from obsion.extra_models import ExternalProbe\n"
            "def produce():\n"
            "    return ExternalProbe(error_code='not_registered')",
            {
                "aliases.py": ("from obsion.db.types import ErrorCodeType as StoredCode\n"),
                "extra_models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "from obsion.aliases import StoredCode\n"
                    "class ExternalProbe:\n"
                    "    error_code: Mapped[str | None] = mapped_column(\n"
                    "        StoredCode(100)\n"
                    "    )"
                ),
            },
        ),
        (
            "from obsion.extra_models import ExternalProbe\n"
            "def produce():\n"
            "    return ExternalProbe(error_code='not_registered')",
            {
                "extra_models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "import obsion.db.types as db_types\n"
                    "class ExternalProbe:\n"
                    "    error_code: Mapped[str | None] = mapped_column(\n"
                    "        db_types.ErrorCodeType(100)\n"
                    "    )"
                )
            },
        ),
        (
            "from obsion.extra_models import ExternalProbe\n"
            "def produce():\n"
            "    return ExternalProbe(error_code='not_registered')",
            {
                "extra_models.py": (
                    "from sqlalchemy import orm\n"
                    "from obsion.db.types import ErrorCodeType\n"
                    "class ExternalProbe:\n"
                    "    error_code: orm.Mapped[str | None] = orm.mapped_column(\n"
                    "        ErrorCodeType(100)\n"
                    "    )"
                )
            },
        ),
    )
    for producer, extra_sources in fixtures:
        with pytest.raises(
            StaticContractAnalysisError,
            match="ErrorCodeType.*canonical|canonical.*ErrorCodeType",
        ):
            analyze_error_producers(
                _sources(producer, **extra_sources),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_dynamic_error_code_type_column_wiring() -> None:
    fixtures = (
        (
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from obsion.db.types import ErrorCodeType\n"
            "column_type = ErrorCodeType(100)\n"
            "class Probe:\n"
            "    error_code: Mapped[str | None] = mapped_column(column_type)\n"
            "class Schedule:\n"
            "    last_error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )"
        ),
        (
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from obsion.db.types import ErrorCodeType\n"
            "def build_type():\n"
            "    return ErrorCodeType(100)\n"
            "class Probe:\n"
            "    error_code: Mapped[str | None] = mapped_column(build_type())\n"
            "class Schedule:\n"
            "    last_error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )"
        ),
    )
    for model_source in fixtures:
        sources = _sources("def produce():\n    return None")
        sources["db/models.py"] = model_source
        with pytest.raises(
            StaticContractAnalysisError,
            match="must use.*ErrorCodeType|ErrorCodeType reference escapes",
        ):
            analyze_error_producers(sources, catalog_codes=_catalog())


def test_analyzer_rejects_error_code_type_reference_escapes() -> None:
    fixtures = (
        (
            "aliases.py",
            "from obsion.db.types import ErrorCodeType as StoredCode\n"
            "EXPORTED_TYPES = (StoredCode,)\n",
        ),
        (
            "extra.py",
            "from obsion.db.types import ErrorCodeType\nStoredCode = ErrorCodeType\n",
        ),
    )
    for path, source in fixtures:
        with pytest.raises(
            StaticContractAnalysisError,
            match="ErrorCodeType.*direct mapped_column|reference escapes",
        ):
            analyze_error_producers(
                _sources("def produce():\n    return None", **{path: source}),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_shadowed_or_nested_error_code_type_wiring() -> None:
    fixtures: tuple[tuple[str, str], ...] = (
        (
            "db/models.py",
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from obsion.db.types import ErrorCodeType\n"
            "class FakeType:\n"
            "    pass\n"
            "ErrorCodeType = FakeType\n"
            "class Probe:\n"
            "    error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )\n"
            "class Schedule:\n"
            "    last_error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )",
        ),
        (
            "db/models.py",
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from obsion.db.types import ErrorCodeType\n"
            "def mapped_column(*values):\n"
            "    return values\n"
            "class Probe:\n"
            "    error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )\n"
            "class Schedule:\n"
            "    last_error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )",
        ),
        (
            "extra_models.py",
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "class ExternalProbe:\n"
            "    from obsion.db.types import ErrorCodeType\n"
            "    error_code: Mapped[str | None] = mapped_column(\n"
            "        ErrorCodeType(100)\n"
            "    )",
        ),
    )
    for path, source in fixtures:
        with pytest.raises(
            StaticContractAnalysisError,
            match="shadowed|nested ErrorCodeType import|must use.*ErrorCodeType",
        ):
            analyze_error_producers(
                _sources("def produce():\n    return None", **{path: source}),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_qualified_and_mispositioned_error_code_type() -> None:
    with pytest.raises(
        StaticContractAnalysisError,
        match="ErrorCodeType reference escapes",
    ):
        analyze_error_producers(
            _sources(
                "def produce():\n    return None",
                **{
                    "extra.py": (
                        "import obsion.db.types as db_types\n"
                        "COLUMN_TYPE = db_types.ErrorCodeType(100)\n"
                    )
                },
            ),
            catalog_codes=_catalog(),
        )

    sources = _sources("def produce():\n    return None")
    sources["db/models.py"] = """
from sqlalchemy.orm import Mapped, mapped_column
from obsion.db.types import ErrorCodeType

class Probe:
    error_code: Mapped[str | None] = mapped_column(str, ErrorCodeType(100))

class Schedule:
    last_error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
"""
    with pytest.raises(
        StaticContractAnalysisError,
        match="must use.*ErrorCodeType|direct mapped_column",
    ):
        analyze_error_producers(sources, catalog_codes=_catalog())


def test_analyzer_requires_canonical_error_fields_to_use_error_code_type() -> None:
    sources = _sources("def produce():\n    return None")
    sources["db/models.py"] = """
from sqlalchemy.orm import Mapped, mapped_column
from obsion.db.types import ErrorCodeType

class Probe:
    error_code: Mapped[str | None] = mapped_column()

class Schedule:
    last_error_code: Mapped[str | None] = mapped_column(ErrorCodeType(100))
"""
    with pytest.raises(StaticContractAnalysisError, match="must use.*ErrorCodeType"):
        analyze_error_producers(sources, catalog_codes=_catalog())


def test_analyzer_allows_unrelated_mapped_column_types() -> None:
    analysis = analyze_error_producers(
        _sources(
            "def produce():\n    return None",
            **{
                "extra_models.py": (
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "class BusinessCodeType:\n"
                    "    pass\n"
                    "class ExternalProbe:\n"
                    "    code: Mapped[str | None] = mapped_column(\n"
                    "        BusinessCodeType()\n"
                    "    )"
                )
            },
        ),
        catalog_codes=_catalog(),
    )
    assert analysis.active_origin_codes == set()


def test_analyzer_resolves_result_and_persisted_field_origins() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.capabilities.gateway import GatewayResult
from obsion.db.models import Probe, Schedule

def produce():
    result = GatewayResult(error_code="capability_failed")
    probe = Probe(error_code="run_failed")
    schedule = Schedule(last_error_code=None)
    probe.error_code = "run_timeout"
    schedule.last_error_code = "schedule_failed"
    return result, probe, schedule
"""
        ),
        catalog_codes=_catalog(
            "capability_failed",
            "run_failed",
            "run_timeout",
            "schedule_failed",
        ),
    )

    assert analysis.origin_sinks == {
        "producer.py::produce#GatewayResult[1]": frozenset({"capability_failed"}),
        "producer.py::produce#Probe.error_code[1]": frozenset({"run_failed"}),
        "producer.py::produce#Probe.error_code[2]": frozenset({"run_timeout"}),
        "producer.py::produce#Schedule.last_error_code[2]": frozenset({"schedule_failed"}),
    }
    assert "producer.py::produce#Schedule.last_error_code[1]" not in analysis.origin_sinks


def test_analyzer_resolves_error_body_origins_and_typed_forwarding() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.api.schemas import ErrorBody
from obsion.common.errors import ObsionError


def direct():
    return ErrorBody(
        code="request_validation_failed",
        message="invalid",
        correlation_id="request-id",
    )


def forward(exc: ObsionError):
    return ErrorBody(
        code=exc.code,
        message=exc.message,
        correlation_id="request-id",
    )
"""
        ),
        catalog_codes=_catalog("request_validation_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::direct#ErrorBody[1]": frozenset({"request_validation_failed"})
    }
    assert analysis.forwarding_sinks["producer.py::forward#ErrorBody[1]"].endswith(":exc.code")


def test_analyzer_allows_error_body_only_as_inline_fastapi_response_model() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from fastapi import FastAPI
from obsion.api.schemas import ErrorBody


def build():
    return FastAPI(
        responses={
            404: {
                "model": ErrorBody,
                "description": "not found",
            },
            500: {
                "model": ErrorBody,
                "description": "internal failure",
            },
        }
    )
"""
        ),
        catalog_codes=_catalog(),
    )

    assert analysis.origin_sinks == {}
    assert analysis.forwarding_sinks == {}
    assert analysis.helper_caller_codes == {}


@pytest.mark.parametrize(
    "producer",
    [
        """
from obsion.api.schemas import ErrorBody

RESPONSES = {404: {"model": ErrorBody}}

def build():
    return RESPONSES
""",
        """
from fastapi import FastAPI
from obsion.api.schemas import ErrorBody


def build():
    responses = {404: {"model": ErrorBody}}
    return FastAPI(responses=responses)
""",
        """
from fastapi import FastAPI
from obsion.api.schemas import ErrorBody


def build():
    return FastAPI(responses={404: {"schema": ErrorBody}})
""",
        """
from fastapi import FastAPI
from obsion.api.schemas import ErrorBody


def build():
    alias = ErrorBody
    return FastAPI(responses={404: {"model": alias}})
""",
        """
from fastapi import FastAPI as Application
from obsion.api.schemas import ErrorBody


def build():
    def Application(**kwargs):
        return kwargs
    return Application(responses={404: {"model": ErrorBody}})
""",
    ],
)
def test_analyzer_rejects_error_body_schema_reference_escapes(producer: str) -> None:
    with pytest.raises(StaticContractAnalysisError, match="reference escapes"):
        analyze_error_producers(_sources(producer), catalog_codes=_catalog())


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("'not_registered'", "unregistered Error code"),
        ("""None""", "non-nullable"),
        ("request.code", "no trusted type|untyped"),
        ("exc.detail", "untrusted type|no trusted type|unsupported untyped"),
    ],
)
def test_analyzer_rejects_untrusted_error_body_codes(
    value: str,
    message: str,
) -> None:
    with pytest.raises(StaticContractAnalysisError, match=message):
        analyze_error_producers(
            _sources(
                f"""
from obsion.api.schemas import ErrorBody

class Request:
    code: str

class HTTPException:
    detail: str


def produce(request: Request, exc: HTTPException):
    return ErrorBody(
        code={value},
        message="invalid",
        correlation_id="request-id",
    )
"""
            ),
            catalog_codes=_catalog(),
        )


def test_analyzer_infers_persisted_models_through_query_collections() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from sqlalchemy import select
from obsion.db.models import Probe

async def produce(session):
    all_probes = list(await session.scalars(select(Probe)))
    active = [probe for probe in all_probes if probe is not None]
    for probe in active:
        probe.error_code = "dependency_failed"
"""
        ),
        catalog_codes=_catalog("dependency_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::produce#Probe.error_code[1]": frozenset({"dependency_failed"})
    }


def test_analyzer_infers_persisted_models_from_typed_iterable_parameters() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.db.models import Probe

async def produce(probes: list[Probe]):
    for probe in probes:
        probe.error_code = "dependency_failed"
"""
        ),
        catalog_codes=_catalog("dependency_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::produce#Probe.error_code[1]": frozenset({"dependency_failed"})
    }


def test_analyzer_resolves_typed_forwarding_with_literal_fallback() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.capabilities.gateway import GatewayResult

def forward(source: GatewayResult):
    return GatewayResult(error_code=source.error_code or "capability_failed")
"""
        ),
        catalog_codes=_catalog("capability_failed"),
    )

    key = "producer.py::forward#GatewayResult[1]"
    assert analysis.origin_sinks == {key: frozenset({"capability_failed"})}
    assert analysis.forwarding_sinks[key].endswith(":source.error_code")


@pytest.mark.parametrize(
    "carrier, attribute",
    [
        ("object", "code"),
        ("ProtocolFailure", "code"),
        ("S3Error", "code"),
        ("Request", "error_code"),
    ],
)
def test_analyzer_rejects_unknown_protocol_external_and_opaque_forwarding(
    carrier: str,
    attribute: str,
) -> None:
    with pytest.raises(
        StaticContractAnalysisError,
        match="unsupported untyped|no trusted type|untrusted type",
    ):
        analyze_error_producers(
            _sources(
                f"""
from obsion.capabilities.gateway import GatewayResult

class ProtocolFailure:
    code: int

class S3Error:
    code: str

class Request:
    error_code: str

def forward(source: {carrier}):
    return GatewayResult(error_code=source.{attribute})
"""
            ),
            catalog_codes=_catalog(),
        )


def test_analyzer_trusts_obsion_error_subclasses_and_persisted_replay_fields() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ObsionError
from obsion.db.models import Probe

class DomainError(ObsionError):
    pass

def from_error(exc: DomainError):
    return Probe(error_code=exc.code)

def replay(source: Probe):
    return Probe(error_code=source.error_code)
"""
        ),
        catalog_codes=_catalog(),
    )

    assert set(analysis.forwarding_sinks) == {
        "producer.py::from_error#Probe.error_code[1]",
        "producer.py::replay#Probe.error_code[1]",
    }


@pytest.mark.parametrize(
    "producer, message",
    [
        (
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n    ValidationError('not_registered', 'invalid')",
            "unregistered Error code",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "def produce(request):\n    ValidationError(request.dynamic, 'invalid')",
            "no trusted type",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "def produce(code):\n    ValidationError(code, 'invalid')",
            "no finite reviewed callers",
        ),
    ],
)
def test_analyzer_fails_closed_on_unregistered_or_unknown_origins(
    producer: str,
    message: str,
) -> None:
    with pytest.raises(StaticContractAnalysisError, match=message):
        analyze_error_producers(_sources(producer), catalog_codes=_catalog())


def test_analyzer_rejects_unbound_branch_and_non_nullable_none() -> None:
    with pytest.raises(StaticContractAnalysisError, match="may be unbound"):
        analyze_error_producers(
            _sources(
                """
from obsion.common.errors import ValidationError

def produce(flag):
    if flag:
        code = "input_invalid"
    ValidationError(code, "invalid")
"""
            ),
            catalog_codes=_catalog("input_invalid"),
        )

    sources = _sources("def produce():\n    return None")
    sources["db/models.py"] += """

class RequiredProbe:
    error_code: Mapped[str] = mapped_column(ErrorCodeType(100))
"""
    sources["producer.py"] = """
from obsion.db.models import RequiredProbe

def produce():
    RequiredProbe(error_code=None)
"""
    with pytest.raises(StaticContractAnalysisError, match="non-nullable"):
        analyze_error_producers(sources, catalog_codes=_catalog())


def test_analyzer_rejects_helper_cycles_depth_overflow_and_reassignment() -> None:
    cyclic = _sources(
        """
from obsion.common.errors import ValidationError

class Service:
    def loop(self, code: str):
        self._fail(code)

    def _fail(self, code: str):
        self.loop(code)
        ValidationError(code, "failed")
"""
    )
    with pytest.raises(StaticContractAnalysisError, match="helper cycle detected"):
        analyze_error_producers(cyclic, catalog_codes=_catalog("failed"))

    deep = _sources(
        """
from obsion.common.errors import ValidationError

class Service:
    def first(self):
        self.second("failed")

    def second(self, code: str):
        self.third(code)

    def third(self, code: str):
        self._fail(code)

    def _fail(self, code: str):
        ValidationError(code, "failed")
"""
    )
    with pytest.raises(StaticContractAnalysisError, match="depth exceeds"):
        analyze_error_producers(
            deep,
            catalog_codes=_catalog("failed"),
            max_helper_depth=2,
        )

    reassigned = _sources(
        """
from obsion.common.errors import ValidationError

class Service:
    def call(self):
        self._fail("failed")

    def _fail(self, code: str):
        code = next(iter(payload))
        ValidationError(code, "failed")
"""
    )
    with pytest.raises(StaticContractAnalysisError, match="parameter 'code' is reassigned"):
        analyze_error_producers(reassigned, catalog_codes=_catalog("failed"))


def test_analyzer_rejects_typed_sinks_outside_reviewed_function_scope() -> None:
    with pytest.raises(StaticContractAnalysisError, match="outside a reviewed function"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "HIDDEN = ValidationError('input_invalid', 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_rejects_qualified_local_aliased_and_escaped_sink_bindings() -> None:
    fixtures = (
        (
            "import obsion.common.errors as errors\n"
            "def produce(request):\n"
            "    errors.ValidationError(request.dynamic, 'invalid')",
            "module-qualified",
        ),
        (
            "from obsion import common\n"
            "def produce(request):\n"
            "    common.errors.ValidationError(request.dynamic, 'invalid')",
            "package-qualified",
        ),
        (
            "def produce():\n"
            "    from obsion.common.errors import ValidationError\n"
            "    ValidationError('input_invalid', 'invalid')",
            "function-local",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    alias = ValidationError\n"
            "    alias('input_invalid', 'invalid')",
            "reference escapes",
        ),
    )
    for producer, message in fixtures:
        with pytest.raises(StaticContractAnalysisError, match=message):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_resolves_relative_and_reexported_error_sinks() -> None:
    producers: tuple[tuple[str, dict[str, str]], ...] = (
        (
            "from .common.errors import ValidationError\n"
            "def produce():\n"
            "    return ValidationError('not_registered', 'invalid')",
            {},
        ),
        (
            "from obsion.aliases import DomainError\n"
            "def produce():\n"
            "    return DomainError('not_registered', 'invalid')",
            {"aliases.py": ("from obsion.common.errors import ValidationError as DomainError\n")},
        ),
        (
            "from obsion.errors import ValidationError\n"
            "def produce():\n"
            "    return ValidationError('not_registered', 'invalid')",
            {"errors/__init__.py": ("from obsion.common.errors import ValidationError\n")},
        ),
    )
    for producer, extra_sources in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="unregistered Error code",
        ):
            analyze_error_producers(
                _sources(producer, **extra_sources),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_class_and_default_argument_sink_aliases() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "class Factory:\n"
        "    fail = ValidationError\n"
        "def produce():\n"
        "    Factory.fail('not_registered', 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce(factory=ValidationError):\n"
        "    factory('not_registered', 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "class Service:\n"
        "    def produce(self, factory=ValidationError):\n"
        "        factory('not_registered', 'invalid')",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="canonical Error sink reference escapes",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_function_scope_sink_shadowing() -> None:
    fixtures = (
        "def produce():\n"
        "    from unrelated import ValidationError\n"
        "    ValidationError('input_invalid', 'invalid')",
        "def produce():\n"
        "    try:\n"
        "        raise RuntimeError()\n"
        "    except RuntimeError as ValidationError:\n"
        "        ValidationError('input_invalid', 'invalid')",
        "def produce(value):\n"
        "    match value:\n"
        "        case ValidationError:\n"
        "            ValidationError('input_invalid', 'invalid')",
    )
    for fixture in fixtures:
        with pytest.raises(StaticContractAnalysisError, match="shadowed"):
            analyze_error_producers(
                _sources("from obsion.common.errors import ValidationError\n" + fixture),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_rejects_required_none_and_nested_scope_sinks() -> None:
    with pytest.raises(StaticContractAnalysisError, match="non-nullable"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce():\n    ValidationError(None, 'invalid')"
            ),
            catalog_codes=_catalog(),
        )

    fixtures = (
        "[ValidationError(code, 'invalid') for code in values]",
        "lambda code: ValidationError(code, 'invalid')",
    )
    for expression in fixtures:
        with pytest.raises(
            StaticContractAnalysisError,
            match="inside comprehensions|inside lambdas",
        ):
            analyze_error_producers(
                _sources(
                    "from obsion.common.errors import ValidationError\n"
                    f"def produce(values):\n    return {expression}"
                ),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_resolves_defaults_in_helper_definition_scope() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ValidationError

DEFAULT_CODE = "default_failed"

def fail(code: str = DEFAULT_CODE):
    ValidationError(code, "failed")

def produce():
    DEFAULT_CODE = "caller_failed"
    fail()
"""
        ),
        catalog_codes=_catalog("caller_failed", "default_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::fail#ValidationError[1]": frozenset({"default_failed"})
    }
    assert analysis.helper_caller_codes == {
        "producer.py::produce#fail[1]": frozenset({"default_failed"})
    }


def test_module_helper_parameter_named_self_is_not_treated_as_receiver() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ValidationError

def fail(self: str, code: str):
    ValidationError(code, "failed")

def produce():
    fail("ignored_failed", "actual_failed")
"""
        ),
        catalog_codes=_catalog("actual_failed", "ignored_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::fail#ValidationError[1]": frozenset({"actual_failed"})
    }


def test_analyzer_rejects_escaped_and_unrelated_receiver_helper_calls() -> None:
    fixtures = (
        "class Service:\n"
        "    def publish(self):\n"
        "        emit = self._fail\n"
        "        emit('input_invalid')\n"
        "    def _fail(self, code: str):\n"
        "        ValidationError(code, 'invalid')",
        "class Service:\n"
        "    def publish(self, other):\n"
        "        other._fail('input_invalid')\n"
        "    def _fail(self, code: str):\n"
        "        ValidationError(code, 'invalid')",
    )
    for fixture in fixtures:
        with pytest.raises(
            StaticContractAnalysisError,
            match="escapes a direct call|unsupported receiver",
        ):
            analyze_error_producers(
                _sources("from obsion.common.errors import ValidationError\n" + fixture),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_handles_chained_writes_and_rejects_augmented_persisted_writes() -> None:
    analysis = analyze_error_producers(
        _sources(
            """
from obsion.db.models import Probe

def produce(first: Probe, second: Probe):
    first.error_code = second.error_code = "run_failed"
"""
        ),
        catalog_codes=_catalog("run_failed"),
    )

    assert analysis.origin_sinks == {
        "producer.py::produce#Probe.error_code[1]": frozenset({"run_failed"}),
        "producer.py::produce#Probe.error_code[2]": frozenset({"run_failed"}),
    }

    with pytest.raises(StaticContractAnalysisError, match="augmented persisted"):
        analyze_error_producers(
            _sources(
                "from obsion.db.models import Probe\n"
                "def produce(probe: Probe):\n    probe.error_code += 'run_failed'"
            ),
            catalog_codes=_catalog("run_failed"),
        )


def test_analyzer_tracks_try_flows_and_rejects_dynamic_loop_match_bindings() -> None:
    fixtures = (
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        code = 'output_invalid'\n"
        "        raise RuntimeError()\n"
        "    except RuntimeError:\n"
        "        ValidationError(code, 'invalid')",
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        code = 'output_invalid'\n"
        "    finally:\n"
        "        ValidationError(code, 'invalid')",
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        code = 'output_invalid'\n"
        "        raise ExceptionGroup('invalid', [ValueError()])\n"
        "    except* ValueError:\n"
        "        pass\n"
        "    ValidationError(code, 'invalid')",
    )
    expected_domains = (
        {"output_invalid"},
        {"output_invalid"},
        {"output_invalid"},
    )
    for fixture, expected in zip(fixtures, expected_domains, strict=True):
        analysis = analyze_error_producers(
            _sources("from obsion.common.errors import ValidationError\n" + fixture),
            catalog_codes=_catalog("input_invalid", "output_invalid"),
        )
        assert analysis.active_origin_codes == expected

    dynamic_bindings = (
        "def produce(values):\n"
        "    code = 'input_invalid'\n"
        "    for code in values:\n"
        "        ValidationError(code, 'invalid')",
        "def produce(value):\n"
        "    code = 'input_invalid'\n"
        "    match value:\n"
        "        case code:\n"
        "            ValidationError(code, 'invalid')",
    )
    for fixture in dynamic_bindings:
        with pytest.raises(
            StaticContractAnalysisError,
            match="loop Error definition|match Error definition",
        ):
            analyze_error_producers(
                _sources("from obsion.common.errors import ValidationError\n" + fixture),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_rejects_additional_symbol_flow_and_mutation_bypasses() -> None:
    fixtures = (
        (
            "from obsion.api.schemas import ErrorBody\n"
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    error = ValidationError('input_invalid', 'invalid')\n"
            "    for error.code in ['not_registered']:\n"
            "        pass\n"
            "    return ErrorBody(\n"
            "        code=error.code, message='invalid', correlation_id='request-id'\n"
            "    )",
            "must not be mutated",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "Alias = ValidationError\n"
            "def produce():\n    Alias('input_invalid', 'invalid')",
            "module scope",
        ),
        (
            "def produce():\n"
            "    import obsion.common.errors as errors\n"
            "    errors.ValidationError('input_invalid', 'invalid')",
            "function-local qualified",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "def produce(values):\n"
            "    code = 'input_invalid'\n"
            "    return [ValidationError(code, 'invalid') for code in values]",
            "inside comprehensions",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "CODE = 'input_invalid'\n"
            "def configure(value):\n"
            "    global CODE\n"
            "    CODE = value\n"
            "def produce():\n    ValidationError(CODE, 'invalid')",
            "unresolved Error code name",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "from obsion.capabilities.gateway import GatewayResult\n"
            "def produce(source: GatewayResult, foreign):\n"
            "    source = foreign\n"
            "    ValidationError(source.error_code, 'invalid')",
            "no trusted type|unsupported forwarding carrier",
        ),
        (
            "from typing import Any\n"
            "from obsion.common.errors import ValidationError\n"
            "from obsion.capabilities.gateway import GatewayResult\n"
            "def produce(source: GatewayResult | Any):\n"
            "    ValidationError(source.error_code, 'invalid')",
            "untrusted type",
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "from obsion.capabilities.gateway import GatewayResult\n"
            "def produce():\n"
            "    error = ValidationError('input_invalid', 'invalid')\n"
            "    error.code = 'output_invalid'\n"
            "    GatewayResult(error_code=error.code)",
            "must not be mutated",
        ),
        (
            "from obsion.db.models import Probe\n"
            "def produce(probe: Probe):\n"
            "    probe.__dict__['error_code'] = 'input_invalid'",
            "dynamic persisted",
        ),
        (
            "from obsion.db.models import Probe\n"
            "def produce(probe: Probe):\n"
            "    setattr(probe, 'error_code', 'input_invalid')",
            "dynamic persisted",
        ),
        (
            "from sqlalchemy import update\n"
            "from obsion.db.models import Probe\n"
            "def produce():\n"
            "    return update(Probe).values(error_code='input_invalid')",
            "bulk persisted",
        ),
    )
    for producer, message in fixtures:
        with pytest.raises(StaticContractAnalysisError, match=message):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid", "output_invalid"),
            )


def test_analyzer_tracks_walrus_try_else_break_and_destructured_fields() -> None:
    walrus = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    code = 'input_invalid'\n"
            "    if (code := 'output_invalid'):\n"
            "        ValidationError(code, 'invalid')"
        ),
        catalog_codes=_catalog("input_invalid", "output_invalid"),
    )
    assert walrus.active_origin_codes == {"output_invalid"}

    try_else = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    code = 'input_invalid'\n"
            "    try:\n"
            "        code = 'output_invalid'\n"
            "    except RuntimeError:\n"
            "        pass\n"
            "    else:\n"
            "        ValidationError(code, 'invalid')"
        ),
        catalog_codes=_catalog("input_invalid", "output_invalid"),
    )
    assert try_else.active_origin_codes == {"output_invalid"}

    breaks = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    code = 'input_invalid'\n"
            "    for item in [1]:\n"
            "        code = 'output_invalid'\n"
            "        break\n"
            "        code = 'input_invalid'\n"
            "    ValidationError(code, 'invalid')"
        ),
        catalog_codes=_catalog("input_invalid", "output_invalid"),
    )
    assert breaks.active_origin_codes == {"input_invalid", "output_invalid"}

    persisted = analyze_error_producers(
        _sources(
            "from obsion.db.models import Probe\n"
            "def produce(probe: Probe):\n"
            "    probe.error_code, ignored = 'input_invalid', None"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert persisted.origin_sinks == {
        "producer.py::produce#Probe.error_code[1]": frozenset({"input_invalid"})
    }


def test_analyzer_rejects_dynamic_error_carrier_mutations() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "from obsion.capabilities.gateway import GatewayResult\n"
        "def produce(field):\n"
        "    error = ValidationError('input_invalid', 'invalid')\n"
        "    setattr(error, field, 'output_invalid')\n"
        "    return GatewayResult(error_code=error.code)",
        "from builtins import setattr as assign\n"
        "from obsion.common.errors import ValidationError\n"
        "from obsion.capabilities.gateway import GatewayResult\n"
        "def produce():\n"
        "    error = ValidationError('input_invalid', 'invalid')\n"
        "    assign(error, 'code', 'output_invalid')\n"
        "    return GatewayResult(error_code=error.code)",
        "from obsion.common.errors import ValidationError\n"
        "from obsion.capabilities.gateway import GatewayResult\n"
        "def produce():\n"
        "    error = ValidationError('input_invalid', 'invalid')\n"
        "    vars(error)['code'] = 'output_invalid'\n"
        "    return GatewayResult(error_code=error.code)",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="dynamic Error field mutation",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid", "output_invalid"),
            )


def test_analyzer_rejects_dynamic_mapping_method_and_alias_mutations() -> None:
    producers = (
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    probe.__dict__.update({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    probe.__dict__.setdefault('error_code', 'not_registered')",
        "from builtins import vars as object_vars\n"
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    object_vars(probe)['error_code'] = 'not_registered'",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    attributes = vars(probe)\n"
        "    attributes['error_code'] = 'not_registered'",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    attributes = vars(probe)\n"
        "    attributes.update({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    attributes = probe.__dict__\n"
        "    attributes.setdefault('error_code', 'not_registered')",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    mutate = probe.__dict__.update\n"
        "    mutate({'error_code': 'not_registered'})",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    error = ValidationError('input_invalid', 'invalid')\n"
        "    attributes = vars(error)\n"
        "    attributes.update({'code': 'not_registered'})",
        "import builtins\n"
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    builtins.setattr(probe, 'error_code', 'not_registered')",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="dynamic (persisted )?Error field mutation|dynamic persisted",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_complex_mapping_method_aliases() -> None:
    producers = (
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    mutate = probe.__dict__.update or probe.__dict__.setdefault\n"
        "    mutate({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, flag):\n"
        "    if flag:\n"
        "        mutate = probe.__dict__.update\n"
        "    else:\n"
        "        mutate = vars(probe).update\n"
        "    mutate({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    first = probe.__dict__.update\n"
        "    second = first\n"
        "    mutate = second\n"
        "    mutate({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, flag):\n"
        "    attributes = probe.__dict__ if flag else vars(probe)\n"
        "    mutate = attributes.update\n"
        "    mutate({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    attributes = probe.__dict__ or vars(probe)\n"
        "    mutate = attributes.update\n"
        "    mutate({'error_code': 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, flag):\n"
        "    attributes = vars(probe)\n"
        "    if flag:\n"
        "        attributes = probe.__dict__\n"
        "    mutate = attributes.update\n"
        "    mutate({'error_code': 'not_registered'})",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="dynamic (persisted )?Error field mutation",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_computed_mapping_keys_and_generator_payloads() -> None:
    producers = (
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, field):\n"
        "    probe.__dict__[field] = 'not_registered'",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, field):\n"
        "    probe.__dict__.update({field: 'not_registered'})",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, fields):\n"
        "    probe.__dict__.update(\n"
        "        {field: 'not_registered' for field in fields}\n"
        "    )",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, fields):\n"
        "    probe.__dict__.update(\n"
        "        (field, 'not_registered') for field in fields\n"
        "    )",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, fields):\n"
        "    payload = (\n"
        "        (field, 'not_registered') for field in fields\n"
        "    )\n"
        "    mutate = probe.__dict__.update\n"
        "    mutate(payload)",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="dynamic (persisted )?Error field mutation",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_mapping_setitem_proxies() -> None:
    producers = (
        "import operator\n"
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    operator.setitem(\n"
        "        probe.__dict__, 'error_code', 'not_registered'\n"
        "    )",
        "from operator import setitem as assign\n"
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe, field):\n"
        "    assign(vars(probe), field, 'not_registered')",
        "import operator\n"
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    mutate = operator.setitem\n"
        "    assign = mutate\n"
        "    assign(probe.__dict__, 'error_code', 'not_registered')",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    attributes = vars(probe)\n"
        "    attributes.__setitem__('error_code', 'not_registered')",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    mutate = probe.__dict__.__setitem__\n"
        "    mutate('error_code', 'not_registered')",
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    dict.__setitem__(\n"
        "        probe.__dict__, 'error_code', 'not_registered'\n"
        "    )",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="dynamic (persisted )?Error field mutation",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_allows_unrelated_mapping_setitem_proxies() -> None:
    analysis = analyze_error_producers(
        _sources(
            "import operator\n"
            "class Envelope:\n"
            "    pass\n"
            "def produce(envelope: Envelope):\n"
            "    operator.setitem(\n"
            "        envelope.__dict__, 'error_code', 'business-field'\n"
            "    )\n"
            "    envelope.__dict__.__setitem__(\n"
            "        'error_code', 'business-field'\n"
            "    )\n"
            "    dict.__setitem__(\n"
            "        envelope.__dict__, 'error_code', 'business-field'\n"
            "    )"
        ),
        catalog_codes=_catalog(),
    )
    assert analysis.active_origin_codes == set()


def test_analyzer_allows_complex_unrelated_mapping_method_aliases() -> None:
    analysis = analyze_error_producers(
        _sources(
            "class Envelope:\n"
            "    pass\n"
            "def produce(envelope: Envelope, flag):\n"
            "    attributes = envelope.__dict__ if flag else vars(envelope)\n"
            "    mutate = attributes.update or envelope.__dict__.setdefault\n"
            "    alias = mutate\n"
            "    alias({'error_code': 'business-field'})"
        ),
        catalog_codes=_catalog(),
    )
    assert analysis.active_origin_codes == set()


def test_analyzer_rejects_typed_error_model_copy_updates() -> None:
    with pytest.raises(
        StaticContractAnalysisError,
        match="model_copy",
    ):
        analyze_error_producers(
            _sources(
                "from obsion.api.schemas import ErrorBody\n"
                "def produce(body: ErrorBody):\n"
                "    return body.model_copy(\n"
                "        update={'code': 'not_registered'}\n"
                "    )"
            ),
            catalog_codes=_catalog(),
        )

    analysis = analyze_error_producers(
        _sources(
            "class Envelope:\n"
            "    def model_copy(self, *, update):\n"
            "        return self\n"
            "def produce(envelope: Envelope):\n"
            "    return envelope.model_copy(\n"
            "        update={'code': 'not_an_error_code'}\n"
            "    )"
        ),
        catalog_codes=_catalog(),
    )
    assert analysis.active_origin_codes == set()


def test_analyzer_rejects_nonlocal_and_reflective_module_code_mutations() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    def mutate():\n"
        "        nonlocal code\n"
        "        code = 'not_registered'\n"
        "    mutate()\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    outcome = 'input_invalid'\n"
        "    def mutate():\n"
        "        nonlocal outcome\n"
        "        outcome = 'not_registered'\n"
        "    mutate()\n"
        "    return ValidationError(outcome, 'invalid')",
        "from obsion.aliases import DomainError\n"
        "def produce():\n"
        "    outcome = 'input_invalid'\n"
        "    def mutate():\n"
        "        nonlocal outcome\n"
        "        outcome = 'not_registered'\n"
        "    mutate()\n"
        "    return DomainError(outcome, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "CODE = 'input_invalid'\n"
        "def mutate():\n"
        "    globals()['CODE'] = 'not_registered'\n"
        "def produce():\n"
        "    return ValidationError(CODE, 'invalid')",
    )
    extra_sources = {
        "aliases.py": ("from obsion.common.errors import ValidationError as DomainError\n")
    }
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="nonlocal|reflective module",
        ):
            analyze_error_producers(
                _sources(producer, **extra_sources),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_rejects_transitive_nonlocal_helper_sinks() -> None:
    fixtures: tuple[tuple[str, dict[str, str]], ...] = (
        (
            "from obsion.common.errors import ValidationError\n"
            "def fail(value):\n"
            "    return ValidationError(value, 'invalid')\n"
            "def produce():\n"
            "    outcome = 'input_invalid'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'not_registered'\n"
            "    mutate()\n"
            "    return fail(outcome)",
            {},
        ),
        (
            "from obsion.helpers import fail as reject\n"
            "def produce():\n"
            "    outcome = 'input_invalid'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'not_registered'\n"
            "    mutate()\n"
            "    return reject(outcome)",
            {
                "helpers.py": (
                    "from obsion.common.errors import ValidationError\n"
                    "def fail(value):\n"
                    "    return ValidationError(value, 'invalid')"
                )
            },
        ),
        (
            "from obsion.helper_aliases import reject\n"
            "def produce():\n"
            "    outcome = 'input_invalid'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'not_registered'\n"
            "    mutate()\n"
            "    return reject(outcome)",
            {
                "helpers.py": (
                    "from obsion.common.errors import ValidationError\n"
                    "def fail(value):\n"
                    "    return ValidationError(value, 'invalid')"
                ),
                "helper_aliases.py": "from obsion.helpers import fail as reject",
            },
        ),
        (
            "from obsion.common.errors import ValidationError\n"
            "def terminal(value):\n"
            "    forwarded = value\n"
            "    return ValidationError(forwarded, 'invalid')\n"
            "def relay(payload):\n"
            "    return terminal(payload)\n"
            "def produce():\n"
            "    outcome = 'input_invalid'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'not_registered'\n"
            "    mutate()\n"
            "    forwarded = outcome\n"
            "    return relay(forwarded)",
            {},
        ),
        (
            "from obsion.db.models import Probe\n"
            "def persist(probe: Probe, value):\n"
            "    probe.error_code = value\n"
            "def produce(probe: Probe):\n"
            "    outcome = 'input_invalid'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'not_registered'\n"
            "    mutate()\n"
            "    persist(probe, outcome)",
            {},
        ),
    )
    for producer, extra_sources in fixtures:
        with pytest.raises(StaticContractAnalysisError, match="nonlocal"):
            analyze_error_producers(
                _sources(producer, **extra_sources),
                catalog_codes=_catalog(
                    "input_invalid",
                    "request_validation_failed",
                ),
            )


def test_analyzer_does_not_treat_unrelated_helper_arguments_as_error_data() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.api.schemas import ErrorBody\n"
            "def respond(message, value):\n"
            "    return ErrorBody(\n"
            "        code=value, message=message, correlation_id='request-id'\n"
            "    )\n"
            "def produce():\n"
            "    outcome = 'first-message'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'second-message'\n"
            "    mutate()\n"
            "    return respond(outcome, 'request_validation_failed')"
        ),
        catalog_codes=_catalog("request_validation_failed"),
    )
    assert analysis.active_origin_codes == {"request_validation_failed"}


def test_analyzer_rejects_nested_nonlocal_helper_sinks() -> None:
    with pytest.raises(StaticContractAnalysisError, match="nonlocal"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce():\n"
                "    outcome = 'input_invalid'\n"
                "    def fail(value):\n"
                "        return ValidationError(value, 'invalid')\n"
                "    def mutate():\n"
                "        nonlocal outcome\n"
                "        outcome = 'not_registered'\n"
                "    mutate()\n"
                "    return fail(outcome)"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_rejects_deeply_nested_nonlocal_helper_sinks() -> None:
    with pytest.raises(StaticContractAnalysisError, match="nonlocal"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce():\n"
                "    outcome = 'input_invalid'\n"
                "    def layer():\n"
                "        def mutate():\n"
                "            nonlocal outcome\n"
                "            outcome = 'not_registered'\n"
                "        mutate()\n"
                "    def fail(value):\n"
                "        return ValidationError(value, 'invalid')\n"
                "    layer()\n"
                "    return fail(outcome)"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_rejects_nonlocal_helper_cycles_and_depth_overflow() -> None:
    cyclic = _sources(
        "from obsion.common.errors import ValidationError\n"
        "def first(value):\n"
        "    return second(value)\n"
        "def second(value):\n"
        "    first(value)\n"
        "    return ValidationError(value, 'invalid')\n"
        "def produce():\n"
        "    outcome = 'input_invalid'\n"
        "    def mutate():\n"
        "        nonlocal outcome\n"
        "        outcome = 'not_registered'\n"
        "    mutate()\n"
        "    return first(outcome)"
    )
    with pytest.raises(StaticContractAnalysisError, match="helper cycle"):
        analyze_error_producers(
            cyclic,
            catalog_codes=_catalog("input_invalid"),
        )

    deep = _sources(
        "from obsion.common.errors import ValidationError\n"
        "def terminal(value):\n"
        "    return ValidationError(value, 'invalid')\n"
        "def second(value):\n"
        "    return terminal(value)\n"
        "def first(value):\n"
        "    return second(value)\n"
        "def produce():\n"
        "    outcome = 'input_invalid'\n"
        "    def mutate():\n"
        "        nonlocal outcome\n"
        "        outcome = 'not_registered'\n"
        "    mutate()\n"
        "    return first(outcome)"
    )
    with pytest.raises(StaticContractAnalysisError, match="depth exceeds"):
        analyze_error_producers(
            deep,
            catalog_codes=_catalog("input_invalid"),
            max_helper_depth=2,
        )


def test_analyzer_does_not_reject_unrelated_nonlocal_or_reflective_writes() -> None:
    analysis = analyze_error_producers(
        _sources(
            "def consume(value):\n"
            "    return {'business_value': value}\n"
            "def produce():\n"
            "    outcome = 'first'\n"
            "    def mutate():\n"
            "        nonlocal outcome\n"
            "        outcome = 'second'\n"
            "    mutate()\n"
            "    return consume(outcome)\n"
            "VERSION = 'v1'\n"
            "def mutate_version():\n"
            "    globals()['VERSION'] = 'v2'"
        ),
        catalog_codes=_catalog(),
    )
    assert analysis.active_origin_codes == set()


def test_analyzer_rejects_unmodeled_bulk_persisted_writes() -> None:
    producers = (
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    return update(Probe).values({'error_code': 'input_invalid'})",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    return update(Probe).values({Probe.error_code: 'input_invalid'})",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce(session):\n"
        "    return session.execute(\n"
        "        update(Probe), [{'error_code': 'input_invalid'}]\n"
        "    )",
        "from obsion.db.models import Probe\n"
        "def produce(session):\n"
        "    return session.bulk_update_mappings(\n"
        "        Probe, [{'error_code': 'input_invalid'}]\n"
        "    )",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    return update(Probe).where(True).values(\n"
        "        {'error_code': 'input_invalid'}\n"
        "    )",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    statement = update(Probe)\n"
        "    return statement.values({'error_code': 'input_invalid'})",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    return update(Probe).values(\n"
        "        **{'error_code': 'input_invalid'}\n"
        "    )",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    mapping = {'error_code': 'input_invalid'}\n"
        "    return update(Probe).values(mapping)",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="bulk persisted Error field writes",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_tracks_result_orm_and_error_subclasses() -> None:
    result = analyze_error_producers(
        _sources(
            "from obsion.capabilities.gateway import GatewayResult\n"
            "class ChildResult(GatewayResult):\n"
            "    pass\n"
            "def produce():\n"
            "    return ChildResult(error_code='input_invalid')"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert result.origin_sinks == {
        "producer.py::produce#ChildResult[1]": frozenset({"input_invalid"})
    }

    orm = analyze_error_producers(
        _sources(
            "from obsion.db.models import Probe\n"
            "class ChildProbe(Probe):\n"
            "    pass\n"
            "def produce():\n"
            "    return ChildProbe(error_code='input_invalid')"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert orm.origin_sinks == {
        "producer.py::produce#ChildProbe.error_code[1]": frozenset({"input_invalid"})
    }

    with pytest.raises(
        StaticContractAnalysisError,
        match="outside a reviewed function",
    ):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "class DomainError(ValidationError):\n"
                "    pass\n"
                "VALUE = DomainError('input_invalid', 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_rejects_custom_result_and_orm_constructor_bypasses() -> None:
    producers = (
        "from obsion.capabilities.gateway import GatewayResult\n"
        "class ChildResult(GatewayResult):\n"
        "    def __init__(self, message, code):\n"
        "        super().__init__(error_code=code)\n"
        "def produce():\n"
        "    return ChildResult('input_invalid', 'not_registered')",
        "from obsion.db.models import Probe\n"
        "class ChildProbe(Probe):\n"
        "    def __init__(self, message, code):\n"
        "        super().__init__(error_code=code)\n"
        "def produce():\n"
        "    return ChildProbe('input_invalid', 'not_registered')",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="custom constructor",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_tracks_nested_raise_loop_and_walrus_flows() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce(flag):\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        if flag:\n"
        "            code = 'output_invalid'\n"
        "            raise ValueError()\n"
        "    except ValueError:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        raise ValueError(code := 'output_invalid')\n"
        "    except ValueError:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce(values):\n"
        "    code = 'input_invalid'\n"
        "    for value in values:\n"
        "        code = 'output_invalid'\n"
        "        continue\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def consume(value):\n"
        "    return value\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    consume(code := 'output_invalid')\n"
        "    return ValidationError(code, 'invalid')",
    )
    expected_domains = (
        {"output_invalid"},
        {"output_invalid"},
        {"input_invalid", "output_invalid"},
        {"output_invalid"},
    )
    for producer, expected in zip(producers, expected_domains, strict=True):
        analysis = analyze_error_producers(
            _sources(producer),
            catalog_codes=_catalog("input_invalid", "output_invalid"),
        )
        assert analysis.active_origin_codes == expected


def test_analyzer_rejects_nullable_forwarding_into_non_nullable_sink() -> None:
    with pytest.raises(StaticContractAnalysisError, match="may receive None"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ObsionError, ValidationError\n"
                "def produce(error: ObsionError | None):\n"
                "    return ValidationError(error.code, 'invalid')"
            ),
            catalog_codes=_catalog(),
        )


def test_analyzer_invalidates_isinstance_narrowing_after_reassignment() -> None:
    with pytest.raises(
        StaticContractAnalysisError,
        match="no trusted type|unsupported forwarding carrier|untyped",
    ):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ObsionError, ValidationError\n"
                "def produce(error: object, foreign):\n"
                "    if isinstance(error, ObsionError):\n"
                "        error = foreign\n"
                "        return ValidationError(error.code, 'invalid')"
            ),
            catalog_codes=_catalog(),
        )


def test_analyzer_tracks_loop_carried_and_nested_try_exception_flows() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce(values):\n"
        "    code = 'input_invalid'\n"
        "    for value in values:\n"
        "        ValidationError(code, 'invalid')\n"
        "        code = 'not_registered'",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        try:\n"
        "            code = 'not_registered'\n"
        "            raise RuntimeError()\n"
        "        finally:\n"
        "            pass\n"
        "    except RuntimeError:\n"
        "        ValidationError(code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_tracks_expression_and_with_context_walrus_definitions() -> None:
    producers = (
        "from contextlib import nullcontext\n"
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    with nullcontext(code := 'not_registered'):\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    ignored = (code := 'not_registered')\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    match (code := 'not_registered'):\n"
        "        case _:\n"
        "            pass\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    return ValidationError(\n"
        "        message=(code := 'not_registered'), code=code\n"
        "    )",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_rejects_staticmethod_helper_binding_bypass() -> None:
    with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "class Service:\n"
                "    @staticmethod\n"
                "    def fail(self, code):\n"
                "        return ValidationError(code, 'invalid')\n"
                "    def produce(self):\n"
                "        return self.fail('input_invalid', 'not_registered')"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_preserves_error_argument_capture_order() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    code = 'input_invalid'\n"
            "    return ValidationError(\n"
            "        code=code, message=(code := 'not_registered')\n"
            "    )"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert analysis.active_origin_codes == {"input_invalid"}

    with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce():\n"
                "    code = 'not_registered'\n"
                "    return ValidationError(code, (code := 'input_invalid'))"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_preserves_comparison_chain_short_circuit_state() -> None:
    with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce():\n"
                "    code = 'not_registered'\n"
                "    False == True == (code := 'input_invalid')\n"
                "    return ValidationError(code, 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_tracks_loop_header_and_class_definition_expressions() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    for _ in ((code := 'not_registered'),):\n"
        "        pass\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "async def values():\n"
        "    if False:\n"
        "        yield None\n"
        "async def produce():\n"
        "    code = 'input_invalid'\n"
        "    async for _ in (code := values()):\n"
        "        pass\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    @((lambda cls: cls) if (code := 'not_registered') else (lambda cls: cls))\n"
        "    class Local:\n"
        "        pass\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    class Local((object if (code := 'not_registered') else object)):\n"
        "        pass\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    class Local(\n"
        "        metaclass=(type if (code := 'not_registered') else type)\n"
        "    ):\n"
        "        pass\n"
        "    return ValidationError(code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_tracks_match_subject_and_guard_exception_states() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        match ((code := 'not_registered'), 1 / 0):\n"
        "            case _:\n"
        "                pass\n"
        "    except ZeroDivisionError:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        match 1:\n"
        "            case _ if (code := 'not_registered') and 1 / 0:\n"
        "                pass\n"
        "    except ZeroDivisionError:\n"
        "        return ValidationError(code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_rejects_exception_handler_binding_and_trystar_state_bypasses() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        raise ValueError()\n"
        "    except ValueError as code:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        raise ExceptionGroup('invalid', [ValueError(), TypeError()])\n"
        "    except* ValueError:\n"
        "        code = 'not_registered'\n"
        "    except* TypeError:\n"
        "        return ValidationError(code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_tracks_nested_handler_return_state_into_finally() -> None:
    with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce():\n"
                "    code = 'input_invalid'\n"
                "    try:\n"
                "        try:\n"
                "            raise ValueError()\n"
                "        except ValueError:\n"
                "            code = 'not_registered'\n"
                "            return None\n"
                "    finally:\n"
                "        ValidationError(code, 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_tracks_match_return_state_into_finally() -> None:
    with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def produce(value):\n"
                "    code = 'input_invalid'\n"
                "    try:\n"
                "        match value:\n"
                "            case _:\n"
                "                code = 'not_registered'\n"
                "                return None\n"
                "    finally:\n"
                "        ValidationError(code, 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_rejects_shadowed_isinstance_forwarding() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce(value, isinstance):\n"
        "    if isinstance(value, ValidationError):\n"
        "        return ValidationError(value.code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def isinstance(*values):\n"
        "    return True\n"
        "def produce(value):\n"
        "    if isinstance(value, ValidationError):\n"
        "        return ValidationError(value.code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "isinstance = lambda *values: True\n"
        "def produce(value):\n"
        "    if isinstance(value, ValidationError):\n"
        "        return ValidationError(value.code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="no trusted type|unsupported untyped|untrusted type",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_nullable_forwarding_fields_into_required_sinks() -> None:
    with pytest.raises(StaticContractAnalysisError, match="may receive None"):
        analyze_error_producers(
            _sources(
                "from obsion.capabilities.gateway import GatewayResult\n"
                "from obsion.common.errors import ValidationError\n"
                "def produce(source: GatewayResult):\n"
                "    return ValidationError(source.error_code, 'invalid')"
            ),
            catalog_codes=_catalog(),
        )


def test_analyzer_allows_guarded_optional_error_forwarding() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ObsionError, ValidationError\n"
            "def produce(error: ObsionError | None):\n"
            "    if error is None:\n"
            "        return None\n"
            "    return ValidationError(error.code, 'invalid')"
        ),
        catalog_codes=_catalog(),
    )

    assert set(analysis.forwarding_sinks) == {"producer.py::produce#ValidationError[1]"}


def test_analyzer_rejects_persisted_descriptor_target_writes() -> None:
    producers = (
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    for probe.error_code in ['not_registered']:\n"
        "        pass",
        "from contextlib import nullcontext\n"
        "from obsion.db.models import Probe\n"
        "def produce(probe: Probe):\n"
        "    with nullcontext('not_registered') as probe.error_code:\n"
        "        pass",
        "from obsion.db.models import Probe\ndef produce(probe: Probe):\n    del probe.error_code",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="persisted Error field write",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_legacy_and_dynamic_sqlalchemy_writes() -> None:
    producers = (
        "from obsion.db.models import Probe\n"
        "def produce(session):\n"
        "    return session.query(Probe).update(\n"
        "        {'error_code': 'not_registered'}\n"
        "    )",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce(session, payload):\n"
        "    return session.execute(update(Probe), payload)",
        "from obsion.db.models import Probe\n"
        "def produce(session, payload):\n"
        "    return session.bulk_update_mappings(Probe, payload)",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="bulk persisted Error field writes",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_rejects_unproven_orm_receiver_provenance() -> None:
    producers = (
        "from obsion.db.models import Probe\n"
        "def factory(model):\n"
        "    return object()\n"
        "def produce():\n"
        "    source = factory(Probe)\n"
        "    source.error_code = 'input_invalid'",
        "from obsion.db.models import Probe\n"
        "def audit(*values):\n"
        "    return values\n"
        "def produce(response):\n"
        "    audit('response', Probe)\n"
        "    response.error_code = 'input_invalid'",
    )
    for producer in producers:
        with pytest.raises(
            StaticContractAnalysisError,
            match="untyped persisted|not bound to a typed ORM model",
        ):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_tracks_definition_time_and_with_context_exception_states() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def fail():\n"
        "    raise RuntimeError()\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        @((code := 'not_registered') and fail())\n"
        "        def nested():\n"
        "            pass\n"
        "    except RuntimeError:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def fail():\n"
        "    raise RuntimeError()\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        class Local(\n"
        "            (object if (code := 'not_registered') else object),\n"
        "            metaclass=fail(),\n"
        "        ):\n"
        "            pass\n"
        "    except RuntimeError:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "class Manager:\n"
        "    def __enter__(self):\n"
        "        return None\n"
        "    def __exit__(self, *args):\n"
        "        return False\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        with Manager() if (code := 'not_registered') else Manager():\n"
        "            raise RuntimeError()\n"
        "    except RuntimeError:\n"
        "        return ValidationError(code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_preserves_match_exception_evaluation_order() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        match (1 / 0, (code := 'not_registered')):\n"
        "            case _:\n"
        "                pass\n"
        "    except ZeroDivisionError:\n"
        "        return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        match 1:\n"
        "            case _ if 1 / 0 or (code := 'not_registered'):\n"
        "                pass\n"
        "    except ZeroDivisionError:\n"
        "        return ValidationError(code, 'invalid')",
    )
    for producer in producers:
        analysis = analyze_error_producers(
            _sources(producer),
            catalog_codes=_catalog("input_invalid"),
        )
        assert analysis.active_origin_codes == {"input_invalid"}


def test_analyzer_preserves_return_paths_through_finally() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "def produce():\n"
            "    code = 'input_invalid'\n"
            "    try:\n"
            "        code = 'output_invalid'\n"
            "        return\n"
            "    finally:\n"
            "        ValidationError(code, 'invalid')"
        ),
        catalog_codes=_catalog("input_invalid", "output_invalid"),
    )
    assert analysis.active_origin_codes == {"output_invalid"}


def test_analyzer_preserves_expression_state_at_implicit_exception_points() -> None:
    with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "def fail():\n"
                "    raise RuntimeError()\n"
                "def produce():\n"
                "    code = 'input_invalid'\n"
                "    try:\n"
                "        [(code := 'not_registered'), fail(), (code := 'output_invalid')]\n"
                "    except RuntimeError:\n"
                "        return ValidationError(code, 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid", "output_invalid"),
        )


def test_analyzer_tracks_implicit_exception_paths_into_handlers_and_finally() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        code = 'not_registered'\n"
        "        1 / 0\n"
        "        code = 'input_invalid'\n"
        "    finally:\n"
        "        ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def may_fail():\n"
        "    return None\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        code = 'not_registered'\n"
        "        may_fail()\n"
        "        code = 'input_invalid'\n"
        "    except RuntimeError:\n"
        "        ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        match 1:\n"
        "            case _:\n"
        "                code = 'not_registered'\n"
        "                raise ValueError()\n"
        "    except ValueError:\n"
        "        ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    try:\n"
        "        match 1:\n"
        "            case _:\n"
        "                code = 'not_registered'\n"
        "                1 / 0\n"
        "    except ZeroDivisionError:\n"
        "        ValidationError(code, 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError, match="unregistered Error code"):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_ignores_comprehension_local_module_bindings() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "CODE = 'input_invalid'\n"
            "VALUES = ['output_invalid']\n"
            "SHADOW = [CODE for CODE in VALUES]\n"
            "def produce():\n"
            "    return ValidationError(CODE, 'invalid')"
        ),
        catalog_codes=_catalog("input_invalid", "output_invalid"),
    )
    assert analysis.active_origin_codes == {"input_invalid"}


def test_analyzer_does_not_treat_target_reads_as_persisted_writes() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.db.models import Probe\n"
            "def produce(probe: Probe, mapping):\n"
            "    mapping[probe.error_code] = 'input_invalid'"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert analysis.origin_sinks == {}


def test_analyzer_rejects_untyped_and_mixed_error_receiver_mutations() -> None:
    producers = (
        "def produce(value):\n    setattr(value, 'code', 'not_registered')",
        "def produce(value, field):\n    setattr(value, field, 'not_registered')",
        "def produce(value):\n"
        "    attributes = vars(value)\n"
        "    attributes.update({'error_code': 'not_registered'})",
        "from obsion.api.schemas import ErrorBody\n"
        "class Envelope:\n"
        "    pass\n"
        "def produce(body: ErrorBody | Envelope):\n"
        "    body.model_copy(update={'code': 'not_registered'})",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog(),
            )


def test_analyzer_does_not_treat_shadowed_builtins_as_reflective_writes() -> None:
    producers = (
        "def produce(value, setattr, field):\n    setattr(value, field, 'not_registered')",
        "def produce(value, vars):\n    vars(value)['error_code'] = 'business-field'",
        "def produce(globals):\n    globals()['CODE'] = 'business-field'",
        "def setattr(value, field, payload):\n"
        "    return value\n"
        "def produce(value, field):\n"
        "    setattr(value, field, 'not_registered')",
        "vars = lambda value: {}\n"
        "def produce(value):\n"
        "    vars(value)['error_code'] = 'business-field'",
        "globals = lambda: {}\ndef produce():\n    globals()['CODE'] = 'business-field'",
    )
    for producer in producers:
        analysis = analyze_error_producers(
            _sources(producer),
            catalog_codes=_catalog(),
        )
        assert analysis.active_origin_codes == set()


def test_analyzer_does_not_reject_unrelated_error_field_mutations() -> None:
    producers = (
        "class Envelope:\n"
        "    pass\n"
        "def produce(envelope: Envelope):\n"
        "    envelope.code = 'http-code'\n"
        "    setattr(envelope, 'status', 'ok')",
        "class Envelope:\n"
        "    pass\n"
        "def produce(envelope: Envelope):\n"
        "    setattr(envelope, 'code', 'http-code')\n"
        "    envelope.__setattr__('code', 'http-code')",
        "class Envelope:\n"
        "    pass\n"
        "def produce(envelope: Envelope):\n"
        "    vars(envelope)['code'] = 'http-code'",
        "class Envelope:\n"
        "    pass\n"
        "def produce(envelope: Envelope):\n"
        "    attributes = vars(envelope)\n"
        "    attributes.update({'code': 'http-code'})",
        "class Builder:\n"
        "    def update(self, mapping):\n"
        "        return mapping\n"
        "    def values(self, **mapping):\n"
        "        return mapping\n"
        "def produce(builder: Builder):\n"
        "    builder.update({'error_code': 'business-field'})\n"
        "    builder.values(error_code='business-field')",
        "class Envelope:\n"
        "    def model_copy(self, *, update):\n"
        "        return self\n"
        "def produce(envelope: Envelope):\n"
        "    envelope.model_copy(update={'code': 'http-code'})",
    )
    for producer in producers:
        analysis = analyze_error_producers(
            _sources(producer),
            catalog_codes=_catalog(),
        )
        assert analysis.active_origin_codes == set()


def test_analyzer_rejects_error_sink_multiple_inheritance() -> None:
    with pytest.raises(
        StaticContractAnalysisError,
        match="multiple inheritance",
    ):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "class Mixin:\n"
                "    def __init__(self, code, message):\n"
                "        super().__init__('output_invalid', message)\n"
                "class DomainError(Mixin, ValidationError):\n"
                "    pass\n"
                "def produce():\n"
                "    return DomainError('input_invalid', 'invalid')"
            ),
            catalog_codes=_catalog("input_invalid", "output_invalid"),
        )


def test_analyzer_allows_fixed_code_inheritance_and_keyword_only_codes() -> None:
    fixed = analyze_error_producers(
        _sources(
            "from obsion.common.errors import NotFoundError\n"
            "class Missing(NotFoundError):\n"
            "    pass\n"
            "def produce():\n"
            "    return Missing('run', 'run-1')"
        ),
        catalog_codes=_catalog(),
    )
    assert fixed.origin_sinks == {
        "producer.py::produce#Missing[1]": frozenset({"resource_not_found"})
    }

    keyword_only = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ObsionError\n"
            "class KeywordError(ObsionError):\n"
            "    def __init__(self, *, code: str, message: str):\n"
            "        super().__init__(code, message)\n"
            "def produce():\n"
            "    return KeywordError(code='input_invalid', message='invalid')"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert keyword_only.origin_sinks == {
        "producer.py::produce#KeywordError[1]": frozenset({"input_invalid"})
    }


def test_analyzer_resolves_inherited_method_helper_callers() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "class Service:\n"
            "    def fail(self, code: str):\n"
            "        return ValidationError(code, 'invalid')\n"
            "class ChildService(Service):\n"
            "    def produce(self):\n"
            "        return self.fail('input_invalid')"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert analysis.active_origin_codes == {"input_invalid"}
    assert analysis.helper_caller_codes == {
        "producer.py::ChildService.produce#fail[1]": frozenset({"input_invalid"})
    }


def test_analyzer_discovers_subclasses_and_validates_implicit_code_implementations() -> None:
    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ObsionError\n"
            "class DomainError(ObsionError):\n"
            "    pass\n"
            "def produce():\n"
            "    DomainError('domain_failed', 'failed')"
        ),
        catalog_codes=_catalog("domain_failed"),
    )
    assert analysis.origin_sinks == {
        "producer.py::produce#DomainError[1]": frozenset({"domain_failed"})
    }

    sources = _sources("def produce():\n    return None")
    sources["common/errors.py"] = sources["common/errors.py"].replace(
        'super().__init__("resource_not_found", resource)',
        'super().__init__("different_not_found", resource)',
    )
    with pytest.raises(StaticContractAnalysisError, match="drifted"):
        analyze_error_producers(
            sources,
            catalog_codes=_catalog("different_not_found"),
        )


def test_analyzer_does_not_conflate_same_named_classes_across_modules() -> None:
    with pytest.raises(StaticContractAnalysisError, match="no finite reviewed callers"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "class Service:\n"
                "    def _fail(self, code: str):\n"
                "        ValidationError(code, 'failed')",
                **{
                    "other.py": "class Service:\n"
                    "    def invoke(self):\n"
                    "        self._fail('input_invalid')"
                },
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_respects_method_overrides_and_super_helper_calls() -> None:
    with pytest.raises(StaticContractAnalysisError, match="no finite reviewed callers"):
        analyze_error_producers(
            _sources(
                "from obsion.common.errors import ValidationError\n"
                "class Base:\n"
                "    def fail(self, code: str):\n"
                "        return ValidationError(code, 'invalid')\n"
                "class Child(Base):\n"
                "    def fail(self, code: str):\n"
                "        return None\n"
                "    def produce(self):\n"
                "        return self.fail('not_registered')"
            ),
            catalog_codes=_catalog(),
        )

    analysis = analyze_error_producers(
        _sources(
            "from obsion.common.errors import ValidationError\n"
            "class Base:\n"
            "    def fail(self, code: str):\n"
            "        return ValidationError(code, 'invalid')\n"
            "class Child(Base):\n"
            "    def produce(self):\n"
            "        return super().fail('input_invalid')"
        ),
        catalog_codes=_catalog("input_invalid"),
    )
    assert analysis.active_origin_codes == {"input_invalid"}
    assert analysis.helper_caller_codes == {
        "producer.py::Child.produce#fail[1]": frozenset({"input_invalid"})
    }


def test_analyzer_rejects_short_circuit_default_and_dynamic_bulk_bypasses() -> None:
    producers = (
        "from obsion.common.errors import ValidationError\n"
        "def produce(flag):\n"
        "    code = 'not_registered'\n"
        "    flag and (code := 'input_invalid')\n"
        "    return ValidationError(code, 'invalid')",
        "from obsion.common.errors import ValidationError\n"
        "def produce():\n"
        "    code = 'input_invalid'\n"
        "    def helper(value=(code := 'not_registered')):\n"
        "        return value\n"
        "    return ValidationError(code, 'invalid')",
        "from sqlalchemy import update\n"
        "from obsion.db.models import Probe\n"
        "def produce(payload):\n"
        "    return update(Probe).values(payload)",
        "import sqlalchemy as sa\n"
        "from obsion.db.models import Probe\n"
        "def produce():\n"
        "    return sa.update(Probe).values(error_code='input_invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError):
            analyze_error_producers(
                _sources(producer),
                catalog_codes=_catalog("input_invalid"),
            )


def test_analyzer_rejects_import_shadowed_function_return_inference() -> None:
    with pytest.raises(
        StaticContractAnalysisError,
        match="no trusted type|unsupported forwarding carrier|untyped",
    ):
        analyze_error_producers(
            _sources(
                "from foreign import make\n"
                "from obsion.capabilities.gateway import GatewayResult\n"
                "def outer():\n"
                "    def make() -> GatewayResult:\n"
                "        return GatewayResult(error_code='input_invalid')\n"
                "def produce():\n"
                "    result = make()\n"
                "    return GatewayResult(error_code=result.error_code)"
            ),
            catalog_codes=_catalog("input_invalid"),
        )


def test_analyzer_rejects_fstring_conversion_and_format_specifiers() -> None:
    producers = (
        "raise ValidationError(f'{code!r}', 'invalid')",
        "raise ValidationError(f'{code:>20}', 'invalid')",
    )
    for producer in producers:
        with pytest.raises(StaticContractAnalysisError, match="format conversions"):
            analyze_error_producers(
                _sources(
                    f"""
from obsion.common.errors import ValidationError

def produce():
    code = "input_invalid"
    {producer}
"""
                ),
                catalog_codes=_catalog("input_invalid"),
            )


def test_manifest_surfaces_sink_caller_and_ordinal_drift() -> None:
    baseline = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ValidationError

class Service:
    def call(self):
        self._fail("first_failed")

    def _fail(self, code: str):
        ValidationError(code, "failed")
"""
        ),
        catalog_codes=_catalog("first_failed", "second_failed"),
    )
    changed = analyze_error_producers(
        _sources(
            """
from obsion.common.errors import ValidationError

class Service:
    def call(self):
        self._fail("first_failed")
        self._fail("second_failed")

    def _fail(self, code: str):
        ValidationError("first_failed", "probe")
        ValidationError(code, "failed")
"""
        ),
        catalog_codes=_catalog("first_failed", "second_failed"),
    )

    assert changed.origin_sinks != baseline.origin_sinks
    assert changed.helper_caller_codes != baseline.helper_caller_codes
