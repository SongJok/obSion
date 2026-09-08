from datetime import UTC, datetime

from obsion.domain.run_intent import RunIntent
from obsion.harness.intent_resolution import (
    ExplorationText,
    IntentContextExplorer,
    VisibleIntentContext,
)

NOW = datetime(2026, 9, 4, 10, tzinfo=UTC)


def _intent(route: str, **updates: object) -> RunIntent:
    payload: dict[str, object] = {
        "preparation_stage": "CONTEXT_RESOLVED",
        "domain": route,
        "route": route,
        "intent": route,
        "question": "它为什么慢？",
        "time_range": {
            "start": "2026-09-03T10:00:00+00:00",
            "end": "2026-09-04T10:00:00+00:00",
        },
        "risk": "L2",
    }
    payload.update(updates)
    return RunIntent.model_validate(payload)


def _explore(intent: RunIntent, context: VisibleIntentContext) -> RunIntent:
    return (
        IntentContextExplorer()
        .explore(
            intent,
            context,
            now=NOW,
            clarification_ttl_seconds=3600,
            remaining_execution_seconds=120,
        )
        .intent
    )


def test_explicit_context_ref_wins_after_repository_acl_filtering() -> None:
    explored = _explore(
        _intent("ENGINEERING"),
        VisibleIntentContext(
            context_refs=(
                {"type": "repository", "value": "payments-api"},
                {"type": "repository", "value": "private-denied"},
            ),
            repositories=("checkout-api", "payments-api"),
        ),
    )

    assert explored.preparation_stage == "INTENT_RESOLVED"
    assert explored.resolved_slot("repository").value == "payments-api"  # type: ignore[union-attr]
    assert explored.clarification.active_id is None


def test_latest_visible_context_resolves_pronoun_before_clarification() -> None:
    explored = _explore(
        _intent("OPERATION"),
        VisibleIntentContext(
            conversation=(
                ExplorationText(
                    source="CONVERSATION",
                    source_ref="turn:previous",
                    text="刚才检查的是 checkout-api",
                    confidence_rank=82,
                ),
            )
        ),
    )

    assert explored.resolved_slot("service").value == "checkout-api"  # type: ignore[union-attr]
    assert explored.decision == "PROCEED"


def test_ambiguous_repository_catalog_requests_bounded_visible_options() -> None:
    explored = _explore(
        _intent("ENGINEERING"),
        VisibleIntentContext(
            repositories=("a-api", "b-api", "c-api", "d-api"),
        ),
    )

    active = explored.clarification.active_request()
    assert explored.preparation_stage == "WAITING_USER"
    assert explored.decision == "CLARIFY"
    assert active is not None
    assert active.fields[0].slot == "repository"
    assert [item.label for item in active.fields[0].options] == ["a-api", "b-api", "c-api"]
    assert active.fields[0].allow_free_text is True


def test_missing_service_asks_only_after_visible_sources_are_exhausted() -> None:
    result = IntentContextExplorer().explore(
        _intent("INCIDENT"),
        VisibleIntentContext(
            conversation=(ExplorationText("CONVERSATION", "turn:1", "没有服务标识", 82),),
            memory=(ExplorationText("MEMORY", "memory:1", "调查偏好", 72),),
            workspace=ExplorationText("WORKSPACE", "workspace:1", "性能调查", 62),
        ),
        now=NOW,
        clarification_ttl_seconds=3600,
        remaining_execution_seconds=120,
    )

    active = result.intent.clarification.active_request()
    assert result.explored_sources == (
        "input",
        "context_refs",
        "conversation",
        "memory",
        "workspace",
        "catalog",
    )
    assert active is not None
    assert active.fields[0].reason_code == "missing_service"
    assert active.fields[0].options == []
    assert active.fields[0].allow_free_text is True


def test_metric_ambiguity_never_silently_uses_the_first_catalog_match() -> None:
    metrics = (
        {"id": "11111111-1111-1111-1111-111111111111", "name": "gross", "display_name": "支付金额"},
        {"id": "22222222-2222-2222-2222-222222222222", "name": "net", "display_name": "净支付金额"},
    )
    explored = _explore(
        _intent("DATA", metrics=list(metrics)),
        VisibleIntentContext(metrics=metrics),
    )

    active = explored.clarification.active_request()
    assert active is not None
    assert active.fields[0].slot == "metric"
    assert active.fields[0].allow_free_text is False


def test_planning_input_applies_answered_slots_without_exposing_internal_state() -> None:
    explored = _explore(
        _intent("ENGINEERING", question="检查 payments-api 的调用链"),
        VisibleIntentContext(repositories=("checkout-api", "payments-api")),
    )

    payload = IntentContextExplorer.planning_input(explored)
    assert payload["repository"] == "payments-api"
    assert "resolved_slots" not in payload
    assert "clarification" not in payload
