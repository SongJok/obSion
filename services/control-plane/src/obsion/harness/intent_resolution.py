import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import JsonValue

from obsion.domain.run_intent import (
    MAX_CLARIFICATION_OPTIONS,
    ClarificationField,
    ResolvedSlot,
    RunIntent,
    make_option,
    make_resolved_slot,
    request_clarification,
)
from obsion.security.redaction import redact_text

_SERVICE_TOKEN = re.compile(
    r"(?<![\w.-])([a-z0-9][a-z0-9._-]{1,118}(?:-service|-api|-worker))(?![\w.-])",
    re.IGNORECASE,
)
_STRUCTURED_SLOTS = frozenset({"repository", "service"})


@dataclass(frozen=True, slots=True)
class ExplorationText:
    source: Literal["CONVERSATION", "MEMORY", "WORKSPACE"]
    source_ref: str
    text: str
    confidence_rank: int


@dataclass(frozen=True, slots=True)
class VisibleIntentContext:
    context_refs: tuple[dict[str, Any], ...] = ()
    conversation: tuple[ExplorationText, ...] = ()
    memory: tuple[ExplorationText, ...] = ()
    workspace: ExplorationText | None = None
    repositories: tuple[str, ...] = ()
    metrics: tuple[dict[str, JsonValue], ...] = ()
    previous_slots: tuple[ResolvedSlot, ...] = ()
    previous_source_ref: str = ""
    current_question: str | None = None


@dataclass(frozen=True, slots=True)
class IntentExploration:
    intent: RunIntent
    explored_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    slot: str
    value: JsonValue
    label: str
    source: Literal[
        "INPUT",
        "CONTEXT_REF",
        "CONVERSATION",
        "MEMORY",
        "WORKSPACE",
        "CATALOG",
    ]
    source_ref: str
    confidence_rank: int

    def resolved(self) -> ResolvedSlot:
        return make_resolved_slot(
            slot=self.slot,
            value=self.value,
            source=self.source,
            source_ref=self.source_ref,
            confidence_rank=self.confidence_rank,
        )


class IntentContextExplorer:
    """Resolve blocking intent slots only from already-authorized, visible context."""

    def explore(
        self,
        intent: RunIntent,
        context: VisibleIntentContext,
        *,
        now: datetime,
        clarification_ttl_seconds: int,
        remaining_execution_seconds: int,
        required_slots: tuple[str, ...] | None = None,
    ) -> IntentExploration:
        candidates: dict[str, list[_Candidate]] = {"metric": [], "repository": [], "service": []}
        explored = ["input", "context_refs", "conversation", "memory", "workspace"]
        self._input_candidates(intent, context, candidates)
        self._context_ref_candidates(context.context_refs, context.repositories, candidates)
        self._visible_text_candidates(context, candidates)
        self._catalog_candidates(context, candidates)
        unavailable = self._previous_candidates(context, candidates)

        resolved = {item.slot: item for item in intent.resolved_slots}
        fields: list[ClarificationField] = []
        for slot in required_slots if required_slots is not None else self._blocking_slots(intent):
            if slot in resolved:
                continue
            ranked = self._ranked(candidates[slot])
            if len(ranked) == 1:
                resolved[slot] = ranked[0].resolved()
                continue
            field = self._clarification_field(slot, ranked)
            if slot in unavailable:
                field = field.model_copy(update={"reason_code": f"previous_{slot}_unavailable"})
            fields.append(field)

        explored.append("catalog")
        updated = intent.model_copy(
            update={
                "resolved_slots": [resolved[key] for key in sorted(resolved)],
                "preparation_stage": "INTENT_RESOLVED",
                "decision": "PROCEED",
                "decision_reason": "context_exploration_complete",
            }
        )
        if fields:
            updated = request_clarification(
                updated,
                question=self._clarification_question(fields),
                fields=fields,
                requested_at=now,
                expires_at=now + timedelta(seconds=clarification_ttl_seconds),
                remaining_execution_seconds=remaining_execution_seconds,
            )
        return IntentExploration(
            intent=RunIntent.model_validate(updated.model_dump(mode="python")),
            explored_sources=tuple(explored),
        )

    @staticmethod
    def _previous_candidates(
        context: VisibleIntentContext,
        candidates: dict[str, list[_Candidate]],
    ) -> set[str]:
        """Carry selections, never old authority or execution/approval state."""
        unavailable: set[str] = set()
        if not context.previous_source_ref:
            return unavailable
        authorized = {item.casefold(): item for item in context.repositories}
        for item in context.previous_slots:
            if item.slot not in _STRUCTURED_SLOTS or not isinstance(item.value, str):
                continue
            value = item.value
            if item.slot == "repository":
                current = authorized.get(value.casefold())
                if current is None:
                    unavailable.add(item.slot)
                    # A revoked previous selection must not fall back to another
                    # repository merely because only one is left in the catalog.
                    candidates[item.slot] = [
                        candidate
                        for candidate in candidates[item.slot]
                        if candidate.source in {"INPUT", "CONTEXT_REF"}
                    ]
                    continue
                value = current
            candidates[item.slot].append(
                _Candidate(
                    item.slot,
                    value,
                    value,
                    "CONVERSATION",
                    context.previous_source_ref,
                    89,
                )
            )
        return unavailable

    @staticmethod
    def planning_input(intent: RunIntent) -> dict[str, Any]:
        payload = intent.model_dump(
            mode="python",
            exclude={"clarification", "resolved_slots", "preparation_stage"},
        )
        for item in intent.resolved_slots:
            if item.slot == "metric" and isinstance(item.value, dict):
                payload["metrics"] = [item.value]
            elif item.slot == "time_range" and isinstance(item.value, dict):
                payload["time_range"] = item.value
            else:
                payload[item.slot] = item.value
        return payload

    @staticmethod
    def mark_planned(intent: RunIntent) -> RunIntent:
        planned = intent.model_copy(
            update={
                "preparation_stage": "PLANNED",
                "decision": "PROCEED",
                "decision_reason": "governed_plan_created",
            }
        )
        return RunIntent.model_validate(planned.model_dump(mode="python"))

    @staticmethod
    def _blocking_slots(intent: RunIntent) -> tuple[str, ...]:
        slots: list[str] = []
        if intent.route in {"DATA", "ANALYTICS"} and len(intent.metrics) != 1:
            slots.append("metric")
        if intent.route == "ENGINEERING":
            slots.append("repository")
        if intent.route in {"INCIDENT", "OPERATION"}:
            slots.append("service")
        return tuple(slots)

    def _input_candidates(
        self,
        intent: RunIntent,
        context: VisibleIntentContext,
        candidates: dict[str, list[_Candidate]],
    ) -> None:
        current_question = (
            context.current_question if context.current_question is not None else intent.question
        )
        question = current_question.casefold()
        for metric in intent.metrics:
            label = str(metric.get("display_name") or metric.get("name") or "").strip()
            if label:
                candidates["metric"].append(
                    _Candidate("metric", metric, label, "INPUT", "turn.input", 90)
                )
        for repository in context.repositories:
            if repository.casefold() in question:
                candidates["repository"].append(
                    _Candidate(
                        "repository",
                        repository,
                        repository,
                        "INPUT",
                        "turn.input",
                        90,
                    )
                )
        for service in self._service_tokens(current_question):
            candidates["service"].append(
                _Candidate("service", service, service, "INPUT", "turn.input", 90)
            )

    def _context_ref_candidates(
        self,
        context_refs: tuple[dict[str, Any], ...],
        repositories: tuple[str, ...],
        candidates: dict[str, list[_Candidate]],
    ) -> None:
        authorized_repositories = {item.casefold(): item for item in repositories}
        for index, reference in enumerate(context_refs):
            reference_type = str(reference.get("type") or "").strip().casefold()
            slot = (
                str(reference.get("slot") or "").strip().casefold()
                if reference_type == "intent_slot"
                else reference_type
            )
            if slot not in _STRUCTURED_SLOTS:
                continue
            value = reference.get("value", reference.get("name"))
            if not isinstance(value, str) or not value.strip():
                continue
            normalized = redact_text(value.strip())
            if slot == "repository":
                authorized = authorized_repositories.get(normalized.casefold())
                if authorized is None:
                    continue
                normalized = authorized
            candidates[slot].append(
                _Candidate(
                    slot,
                    normalized,
                    normalized,
                    "CONTEXT_REF",
                    f"turn.context_refs[{index}]",
                    95,
                )
            )

    def _visible_text_candidates(
        self,
        context: VisibleIntentContext,
        candidates: dict[str, list[_Candidate]],
    ) -> None:
        sources = [*context.conversation, *context.memory]
        if context.workspace is not None:
            sources.append(context.workspace)
        for source in sources:
            normalized = source.text.casefold()
            for repository in context.repositories:
                if repository.casefold() in normalized:
                    candidates["repository"].append(
                        _Candidate(
                            "repository",
                            repository,
                            repository,
                            source.source,
                            source.source_ref,
                            source.confidence_rank,
                        )
                    )
            for service in self._service_tokens(source.text):
                candidates["service"].append(
                    _Candidate(
                        "service",
                        service,
                        service,
                        source.source,
                        source.source_ref,
                        source.confidence_rank,
                    )
                )

    @staticmethod
    def _catalog_candidates(
        context: VisibleIntentContext,
        candidates: dict[str, list[_Candidate]],
    ) -> None:
        for repository in context.repositories:
            candidates["repository"].append(
                _Candidate(
                    "repository",
                    repository,
                    repository,
                    "CATALOG",
                    f"code-repository:{repository}",
                    50,
                )
            )
        for metric in context.metrics:
            label = str(metric.get("display_name") or metric.get("name") or "").strip()
            if label:
                candidates["metric"].append(
                    _Candidate(
                        "metric",
                        metric,
                        label,
                        "CATALOG",
                        f"metric:{metric.get('id', metric.get('name', label))}",
                        50,
                    )
                )

    @staticmethod
    def _ranked(items: list[_Candidate]) -> list[_Candidate]:
        by_value: dict[str, _Candidate] = {}
        for item in items:
            key = repr(item.value)
            current = by_value.get(key)
            if current is None or item.confidence_rank > current.confidence_rank:
                by_value[key] = item
        if not by_value:
            return []
        highest = max(item.confidence_rank for item in by_value.values())
        return sorted(
            (item for item in by_value.values() if item.confidence_rank == highest),
            key=lambda item: item.label.casefold(),
        )

    @staticmethod
    def _clarification_field(slot: str, candidates: list[_Candidate]) -> ClarificationField:
        prompts = {
            "metric": "请选择本次分析使用的指标",
            "repository": "请选择需要调查的代码库",
            "service": "请选择需要调查的服务",
        }
        options = [
            make_option(label=item.label, canonical_value=item.value)
            for item in candidates[:MAX_CLARIFICATION_OPTIONS]
        ]
        return ClarificationField(
            slot=slot,
            reason_code=(
                f"multiple_{slot}_candidates" if len(candidates) > 1 else f"missing_{slot}"
            ),
            prompt=prompts[slot],
            options=options,
            allow_free_text=slot in {"repository", "service"},
        )

    @staticmethod
    def _clarification_question(fields: list[ClarificationField]) -> str:
        labels = {
            "metric": "指标",
            "repository": "代码库",
            "service": "服务",
        }
        requested = "、".join(labels[item.slot] for item in fields)
        return (
            "我已检查当前问题、任务历史、工作区上下文和可访问目录；"
            f"还需要确认{requested}后才能安全继续。"
        )

    @staticmethod
    def _service_tokens(value: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(match.casefold() for match in _SERVICE_TOKEN.findall(value)))
