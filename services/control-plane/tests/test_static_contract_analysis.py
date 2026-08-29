from __future__ import annotations

import pytest
from static_contract_analysis import (
    StaticContractAnalysisError,
    analyze_event_producers,
)

_EVENT_DRAFT = """
from dataclasses import dataclass

@dataclass
class EventDraft:
    name: str
    schema_version: int = 1
"""


def _sources(source: str, *, enums: str = "") -> dict[str, str]:
    return {
        "persistence/events.py": _EVENT_DRAFT,
        "domain/enums.py": f"from enum import StrEnum\n{enums}",
        "producer.py": f"from obsion.persistence.events import EventDraft\n{source}",
    }


def test_analyzer_resolves_literal_conditional_helper_and_explicit_version() -> None:
    result = analyze_event_producers(
        _sources(
            """
class Service:
    def direct(self, revised: bool):
        EventDraft(
            name="run.feedback.recorded" if revised else "run.feedback.revised",
        )
        EventDraft(name="run.completed", schema_version=2)

    def first(self):
        self._event("run.started")

    def second(self):
        self._event("run.resumed" if ready else "run.started")

    def _event(self, name: str):
        EventDraft(name=name)
"""
        )
    )

    assert result.sink_pairs == {
        "producer.py::Service.direct#EventDraft[1]": frozenset(
            {
                ("run.feedback.recorded", 1),
                ("run.feedback.revised", 1),
            }
        ),
        "producer.py::Service.direct#EventDraft[2]": frozenset({("run.completed", 2)}),
        "producer.py::Service._event#EventDraft[1]": frozenset(
            {("run.started", 1), ("run.resumed", 1)}
        ),
    }
    assert result.helper_caller_pairs == {
        "producer.py::Service.first#_event[1]": frozenset({("run.started", 1)}),
        "producer.py::Service.second#_event[1]": frozenset(
            {("run.resumed", 1), ("run.started", 1)}
        ),
    }
    assert result.enum_dependencies == {}


def test_analyzer_uses_last_definite_assignment() -> None:
    result = analyze_event_producers(
        _sources(
            "def produce():\n"
            "    name = 'run.started'\n"
            "    name = 'run.resumed'\n"
            "    EventDraft(name=name)"
        )
    )

    assert result.sink_pairs == {
        "producer.py::produce#EventDraft[1]": frozenset({("run.resumed", 1)})
    }


def test_analyzer_resolves_guarded_enum_fstring_and_fingerprints_all_members() -> None:
    result = analyze_event_producers(
        _sources(
            """
from domain.enums import WorkflowStatus

class Service:
    def publish(self, target: WorkflowStatus):
        if target not in {
            WorkflowStatus.ACTIVE,
            WorkflowStatus.PAUSED,
            WorkflowStatus.RETIRED,
        }:
            raise ValueError("unsupported")
        EventDraft(name=f"workflow.{target.value.lower()}")
""",
            enums="""
class WorkflowStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"
""",
        )
    )

    assert result.sink_pairs == {
        "producer.py::Service.publish#EventDraft[1]": frozenset(
            {
                ("workflow.active", 1),
                ("workflow.paused", 1),
                ("workflow.retired", 1),
            }
        )
    }
    assert result.enum_dependencies == {
        "domain/enums.py::WorkflowStatus": (
            ("DRAFT", "DRAFT"),
            ("ACTIVE", "ACTIVE"),
            ("PAUSED", "PAUSED"),
            ("RETIRED", "RETIRED"),
        )
    }


@pytest.mark.parametrize(
    "schema_version",
    [
        "version",
        "1 if current else 2",
        "True",
        "default_version()",
    ],
)
def test_analyzer_rejects_non_literal_schema_versions(schema_version: str) -> None:
    with pytest.raises(StaticContractAnalysisError, match="schema_version"):
        source = (
            f"def produce():\n    EventDraft(name='run.started', schema_version={schema_version})"
        )
        analyze_event_producers(_sources(source))


@pytest.mark.parametrize(
    "source, message",
    [
        ("def produce(name: str):\n    EventDraft(name=name)", "dynamic module helper"),
        (
            "class Service:\n"
            "    def caller(self, name):\n        self._event(name)\n"
            "    def _event(self, name: str):\n        EventDraft(name=name)",
            "no finite reviewed callers",
        ),
        (
            "class Service:\n"
            "    def caller(self):\n        self._event(*names)\n"
            "    def _event(self, name: str):\n        EventDraft(name=name)",
            r"cannot use \*args",
        ),
    ],
)
def test_analyzer_fails_closed_on_unknown_dynamic_domains(source: str, message: str) -> None:
    with pytest.raises(StaticContractAnalysisError, match=message):
        analyze_event_producers(_sources(source))


@pytest.mark.parametrize(
    "producer, message",
    [
        (
            "def produce():\n"
            "    def EventDraft(**kwargs):\n"
            "        return kwargs\n"
            "    EventDraft(name='run.started')",
            "shadowed",
        ),
        (
            "EventDraft = factory\ndef produce():\n    EventDraft(name='run.started')",
            "shadowed",
        ),
    ],
)
def test_analyzer_rejects_shadowed_event_draft_bindings(
    producer: str,
    message: str,
) -> None:
    with pytest.raises(StaticContractAnalysisError, match=message):
        analyze_event_producers(_sources(producer))


def test_analyzer_resolves_explicit_event_draft_import_alias() -> None:
    sources = _sources("def produce():\n    ED(name='run.started')")
    sources["producer.py"] = (
        "from obsion.persistence.events import EventDraft as ED\n"
        "def produce():\n    ED(name='run.started')"
    )

    result = analyze_event_producers(sources)

    assert result.sink_pairs == {
        "producer.py::produce#EventDraft[1]": frozenset({("run.started", 1)})
    }


def test_analyzer_rejects_unrelated_same_named_constructor() -> None:
    sources = _sources(
        "def produce():\n    return 1",
    )
    sources["unrelated.py"] = (
        "def EventDraft(**kwargs):\n"
        "    return kwargs\n"
        "def produce():\n"
        "    EventDraft(name=request.name)"
    )

    with pytest.raises(StaticContractAnalysisError, match="not bound"):
        analyze_event_producers(sources)


def test_analyzer_resolves_branch_assignments_and_fingerprints_control_enum() -> None:
    result = analyze_event_producers(
        _sources(
            """
from domain.enums import RunStatus

class Run:
    status: RunStatus


def produce(run: Run):
    if run.status == RunStatus.PENDING:
        name = "run.started"
    else:
        name = "run.resumed"
    EventDraft(name=name)
""",
            enums="""
class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
""",
        )
    )

    assert result.sink_pairs == {
        "producer.py::produce#EventDraft[1]": frozenset({("run.resumed", 1), ("run.started", 1)})
    }
    assert result.enum_dependencies == {
        "domain/enums.py::RunStatus": (
            ("PENDING", "PENDING"),
            ("RUNNING", "RUNNING"),
        )
    }


def test_analyzer_handles_assignment_and_sink_on_same_line() -> None:
    result = analyze_event_producers(
        _sources("def produce():\n    name = 'run.started'; EventDraft(name=name)")
    )

    assert result.sink_pairs == {
        "producer.py::produce#EventDraft[1]": frozenset({("run.started", 1)})
    }


def test_analyzer_rejects_unbound_branch_assignment() -> None:
    with pytest.raises(StaticContractAnalysisError, match="may be unbound"):
        analyze_event_producers(
            _sources(
                "def produce(flag):\n"
                "    if flag:\n"
                "        name = 'run.started'\n"
                "    EventDraft(name=name)"
            )
        )


def test_analyzer_rejects_helper_cycle_and_depth_overflow() -> None:
    cyclic = _sources(
        """
class Service:
    def loop(self, name: str):
        self._event(name)

    def _event(self, name: str):
        self.loop(name)
        EventDraft(name=name)
"""
    )
    with pytest.raises(StaticContractAnalysisError, match="helper cycle detected"):
        analyze_event_producers(cyclic)

    deep = _sources(
        """
class Service:
    def first(self):
        self.second("run.started")

    def second(self, name: str):
        self.third(name)

    def third(self, name: str):
        self._event(name)

    def _event(self, name: str):
        EventDraft(name=name)
"""
    )
    with pytest.raises(StaticContractAnalysisError, match="depth exceeds"):
        analyze_event_producers(deep, max_helper_depth=2)


def test_analyzer_resolves_transitive_helpers() -> None:
    result = analyze_event_producers(
        _sources(
            """
class Service:
    def publish(self):
        self.forward("run.started")

    def forward(self, name: str):
        self._event(name)

    def _event(self, name: str):
        EventDraft(name=name)
"""
        )
    )

    assert result.sink_pairs == {
        "producer.py::Service._event#EventDraft[1]": frozenset({("run.started", 1)})
    }
    assert result.helper_caller_pairs == {
        "producer.py::Service.forward#_event[1]": frozenset({("run.started", 1)}),
        "producer.py::Service.publish#forward[1]": frozenset({("run.started", 1)}),
    }


def test_analyzer_resolves_enum_alias_and_rejects_unbound_same_name() -> None:
    aliased = _sources(
        """
from domain.enums import WorkflowStatus as Status

class Service:
    def publish(self, target: Status):
        EventDraft(name=f"workflow.{target.value.lower()}")
""",
        enums="""
class WorkflowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
""",
    )
    result = analyze_event_producers(aliased)
    assert result.enum_dependencies == {
        "domain/enums.py::WorkflowStatus": (
            ("ACTIVE", "ACTIVE"),
            ("PAUSED", "PAUSED"),
        )
    }

    unbound = _sources("def produce(target: WorkflowStatus):\n    EventDraft(name=target.value)")
    with pytest.raises(StaticContractAnalysisError, match="unresolved name"):
        analyze_event_producers(unbound)


def test_analyzer_rejects_escaped_constructor_and_helper_references() -> None:
    escaped_constructor = _sources(
        "def produce():\n    constructor = EventDraft\n    constructor(name='run.started')"
    )
    with pytest.raises(StaticContractAnalysisError, match="reference escapes"):
        analyze_event_producers(escaped_constructor)

    escaped_helper = _sources(
        """
class Service:
    def publish(self):
        emit = self._event
        emit("run.started")

    def _event(self, name: str):
        EventDraft(name=name)
"""
    )
    with pytest.raises(StaticContractAnalysisError, match="escapes a direct call"):
        analyze_event_producers(escaped_helper)


def test_analyzer_rejects_helper_parameter_reassignment() -> None:
    with pytest.raises(StaticContractAnalysisError, match="parameter 'name' is reassigned"):
        analyze_event_producers(
            _sources(
                """
class Service:
    def publish(self):
        self._event("run.started")

    def _event(self, name: str):
        name = next(iter(payload))
        EventDraft(name=name)
"""
            )
        )


def test_analyzer_rejects_non_literal_enum_member() -> None:
    with pytest.raises(StaticContractAnalysisError, match="literal strings"):
        analyze_event_producers(
            _sources(
                "def produce():\n    EventDraft(name='run.started')",
                enums="""
class WorkflowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ALIAS = ACTIVE
""",
            )
        )


def test_analyzer_rejects_function_scope_import_except_and_match_shadowing() -> None:
    fixtures = (
        "def produce():\n    from unrelated import EventDraft\n    EventDraft(name='fake')",
        "def produce():\n"
        "    try:\n"
        "        raise RuntimeError()\n"
        "    except RuntimeError as EventDraft:\n"
        "        EventDraft(name='fake')",
        "def produce(value):\n"
        "    match value:\n"
        "        case EventDraft:\n"
        "            EventDraft(name='fake')",
    )
    for fixture in fixtures:
        with pytest.raises(StaticContractAnalysisError, match="shadowed"):
            analyze_event_producers(_sources(fixture))


def test_analyzer_rejects_event_draft_class_mutation() -> None:
    with pytest.raises(StaticContractAnalysisError, match="must not be mutated"):
        analyze_event_producers(
            _sources(
                "def produce():\n"
                "    EventDraft.schema_version = 2\n"
                "    EventDraft(name='run.started')"
            )
        )


def test_analyzer_rejects_event_draft_outside_function_scope() -> None:
    with pytest.raises(StaticContractAnalysisError, match="outside a reviewed function scope"):
        analyze_event_producers(_sources("_HIDDEN_DRAFT = EventDraft(name='run.started')"))


def test_analyzer_rejects_package_qualified_event_draft_import() -> None:
    sources = _sources("def produce():\n    return None")
    sources["producer.py"] = (
        "from obsion.persistence import events as hidden_events\n"
        "def produce(request):\n"
        "    hidden_events.EventDraft(name=request.name)"
    )
    with pytest.raises(StaticContractAnalysisError, match="package-qualified"):
        analyze_event_producers(sources)


def test_analyzer_rejects_domain_explosion() -> None:
    source = "def produce(flag):\n    EventDraft(name="
    expression = "'event.0'"
    for index in range(1, 8):
        expression = f"({expression} if flag else 'event.{index}')"
    with pytest.raises(StaticContractAnalysisError, match="exceeds 4"):
        analyze_event_producers(
            _sources(source + expression + ")"),
            max_domain=4,
        )


def test_manifest_keys_detect_new_sink_caller_enum_member_and_ordinal_drift() -> None:
    baseline = analyze_event_producers(
        _sources(
            """
from domain.enums import WorkflowStatus

class Service:
    def caller(self):
        self._event("run.started")

    def publish(self, target: WorkflowStatus):
        EventDraft(name=f"workflow.{target.value.lower()}")

    def _event(self, name: str):
        EventDraft(name=name)
""",
            enums="""
class WorkflowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
""",
        )
    )
    changed = analyze_event_producers(
        _sources(
            """
from domain.enums import WorkflowStatus

class Service:
    def caller(self):
        self._event("run.started")
        self._event("run.started")

    def publish(self, target: WorkflowStatus):
        EventDraft(name="probe.literal")
        EventDraft(name=f"workflow.{target.value.lower()}")

    def _event(self, name: str):
        EventDraft(name=name)
""",
            enums="""
class WorkflowStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"
""",
        )
    )

    assert changed.sink_pairs != baseline.sink_pairs
    assert changed.helper_caller_pairs != baseline.helper_caller_pairs
    assert changed.enum_dependencies != baseline.enum_dependencies
