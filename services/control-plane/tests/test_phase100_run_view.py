from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from obsion.api.schemas import RunView
from obsion.domain.enums import RunStatus
from obsion.domain.run_intent import (
    ClarificationField,
    RunIntent,
    make_option,
    request_clarification,
)

NOW = datetime(2026, 9, 4, 9, tzinfo=UTC)


def _run_payload(intent: dict[str, object], status: RunStatus) -> dict[str, object]:
    return {
        "id": uuid4(),
        "turn_id": uuid4(),
        "status": status,
        "agent_version_id": None,
        "model_profile_id": None,
        "prompt_pins": [],
        "context_budget": {},
        "conversation_compact": {},
        "workspace_context": {},
        "intent": intent,
        "plan": {},
        "max_steps": 30,
        "timeout_seconds": 300,
        "max_input_tokens": 120_000,
        "max_output_tokens": 16_000,
        "max_cost_amount": Decimal("10"),
        "step_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_amount": Decimal("0"),
        "started_at": NOW,
        "completed_at": None,
        "cancellation_requested_at": None,
        "error_code": None,
        "error_message": None,
        "replay_of_run_id": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _waiting_intent() -> RunIntent:
    base = RunIntent(
        preparation_stage="CONTEXT_RESOLVED",
        domain="OPERATION",
        route="OPERATION",
        intent="OPERATION",
        question="它为什么慢？",
        time_range={},
        need_root_cause=True,
        risk="L2",
        agent="operations-agent",
    )
    return request_clarification(
        base,
        question="你指的是哪个服务？",
        fields=[
            ClarificationField(
                slot="service",
                reason_code="multiple_service_candidates",
                prompt="请选择服务",
                options=[
                    make_option(
                        label="checkout-api",
                        canonical_value={
                            "service": "checkout-api",
                            "resource_ref": "service://private/checkout-api",
                        },
                    )
                ],
            )
        ],
        requested_at=NOW,
        expires_at=NOW + timedelta(hours=24),
        remaining_execution_seconds=120,
    )


def test_run_view_projects_typed_pending_clarification_without_internal_state() -> None:
    intent = _waiting_intent()
    view = RunView.model_validate(
        _run_payload(intent.model_dump(mode="json"), RunStatus.WAITING_USER)
    )
    dumped = view.model_dump(mode="json")
    serialized = view.model_dump_json()

    assert dumped["intent"]["route"] == "OPERATION"
    assert "clarification" not in dumped["intent"]
    assert dumped["pending_clarification"]["gaps"][0] == {
        "slot": "service",
        "reason_code": "multiple_service_candidates",
        "prompt": "请选择服务",
        "cardinality": "ONE",
        "value_type": "STRING",
        "options": [
            {
                "id": intent.clarification.active_request().fields[0].options[0].id,  # type: ignore[union-attr]
                "label": "checkout-api",
            }
        ],
        "allow_free_text": False,
    }
    for forbidden in (
        "canonical_value",
        "resource_ref",
        "remaining_execution_seconds",
        "gap_fingerprint",
        "answers",
    ):
        assert forbidden not in serialized


def test_run_view_fail_closes_legacy_waiting_user_intent() -> None:
    with pytest.raises(PydanticValidationError):
        RunView.model_validate(_run_payload({}, RunStatus.WAITING_USER))


def test_run_view_preserves_safe_legacy_intent_allowlist() -> None:
    view = RunView.model_validate(
        _run_payload(
            {
                "route": "KNOWLEDGE",
                "question": "safe",
                "private_runtime_state": "hidden",
            },
            RunStatus.COMPLETED,
        )
    )

    assert view.intent == {"route": "KNOWLEDGE", "question": "safe"}
    assert view.pending_clarification is None
