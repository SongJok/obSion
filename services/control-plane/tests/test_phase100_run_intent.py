from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from obsion.domain.enums import RunStatus
from obsion.domain.run_intent import (
    MAX_CLARIFICATION_FIELDS,
    MAX_CLARIFICATION_OPTIONS,
    ClarificationAnswerInvalid,
    ClarificationAnswerItem,
    ClarificationAnswerSubmission,
    ClarificationField,
    ClarificationNoProgress,
    ClarificationOption,
    ClarificationRequest,
    ClarificationStaleRevision,
    ClarificationStateInvalid,
    RunIntent,
    apply_clarification_answer,
    fingerprint_value,
    make_option,
    parse_run_intent,
    pending_clarification_projection,
    public_intent_projection,
    request_clarification,
)

NOW = datetime(2026, 9, 4, 9, tzinfo=UTC)


def _intent() -> RunIntent:
    return RunIntent(
        preparation_stage="CONTEXT_RESOLVED",
        domain="ENGINEERING",
        route="ENGINEERING",
        intent="ANALYTICS_QUERY",
        question="它为什么慢？",
        metrics=[],
        dimensions=[],
        time_range={
            "start": "2026-09-03T09:00:00+00:00",
            "end": "2026-09-04T09:00:00+00:00",
            "timezone": "UTC",
        },
        comparison=None,
        need_data=False,
        need_root_cause=True,
        risk="L1",
        agent="engineering-agent",
        skill="code-investigation",
    )


def _field(
    slot: str = "repository",
    *,
    options: list[ClarificationOption] | None = None,
    allow_free_text: bool = False,
    value_type: Literal["STRING", "TIME_RANGE"] = "STRING",
) -> ClarificationField:
    return ClarificationField(
        slot=slot,
        reason_code=f"multiple_{slot}_candidates",
        prompt=f"请选择 {slot}",
        value_type=value_type,
        options=options
        if options is not None
        else [
            make_option(label="checkout-api", canonical_value="checkout-api"),
            make_option(label="payments-api", canonical_value="payments-api"),
        ],
        allow_free_text=allow_free_text,
    )


def _waiting_intent(
    fields: list[ClarificationField] | None = None,
) -> RunIntent:
    return request_clarification(
        _intent(),
        question="请补充执行所需信息",
        fields=fields or [_field()],
        requested_at=NOW,
        expires_at=NOW + timedelta(hours=24),
        remaining_execution_seconds=173,
    )


def test_strict_intent_round_trips_without_coercion_or_extra_fields() -> None:
    intent = _intent()

    restored = RunIntent.model_validate(intent.model_dump(mode="json"))

    assert restored == intent
    with pytest.raises(PydanticValidationError):
        RunIntent.model_validate({**intent.model_dump(mode="json"), "unknown": True})
    with pytest.raises(PydanticValidationError):
        RunIntent.model_validate({**intent.model_dump(mode="json"), "need_data": "false"})


@pytest.mark.parametrize(
    "slot",
    [
        "password",
        "api_key",
        "API-Key",
        "clientSecret",
        "bearer_token",
        "private-key",
        "ｐａｓｓｗｏｒｄ",
        "authorization_header",
        "database_dsn",
        "private_endpoint",
    ],
)
def test_clarification_rejects_secret_and_private_endpoint_slots(slot: str) -> None:
    with pytest.raises(PydanticValidationError):
        _field(slot=slot)


@pytest.mark.parametrize(
    "slot",
    [
        "route",
        "domain",
        "intent",
        "question",
        "risk",
        "need_data",
        "need_root_cause",
        "agent",
        "skill",
        "clarification",
        "schema_version",
    ],
)
def test_clarification_rejects_server_derived_slots(slot: str) -> None:
    with pytest.raises(PydanticValidationError):
        _field(slot=slot)


def test_clarification_enforces_two_three_three_bounds_and_uniqueness() -> None:
    option = make_option(label="one", canonical_value="one")
    duplicate = option.model_copy()
    with pytest.raises(PydanticValidationError, match="option ids"):
        _field(options=[option, duplicate])
    with pytest.raises(PydanticValidationError):
        _field(
            options=[
                make_option(label=str(value), canonical_value=str(value))
                for value in range(MAX_CLARIFICATION_OPTIONS + 1)
            ]
        )
    with pytest.raises(PydanticValidationError, match="visible options"):
        _field(options=[])

    intent = _intent()
    with pytest.raises(PydanticValidationError):
        request_clarification(
            intent,
            question="too many",
            fields=[_field(f"slot_{value}") for value in range(MAX_CLARIFICATION_FIELDS + 1)],
            requested_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            remaining_execution_seconds=10,
        )

    first = _waiting_intent()
    answer = ClarificationAnswerSubmission(
        expected_intent_revision=first.intent_revision,
        answers=[
            ClarificationAnswerItem(
                slot="repository",
                option_id=first.clarification.active_request().fields[0].options[0].id,  # type: ignore[union-attr]
            )
        ],
    )
    answered = apply_clarification_answer(
        first,
        answer,
        clarification_id=first.clarification.active_id or "",
        answered_by=str(uuid4()),
        answered_at=NOW + timedelta(minutes=1),
    ).intent
    second = request_clarification(
        answered,
        question="还需要 service",
        fields=[_field("service")],
        requested_at=NOW + timedelta(minutes=2),
        expires_at=NOW + timedelta(hours=1),
        remaining_execution_seconds=171,
    )
    assert second.clarification.active_request().round == 2  # type: ignore[union-attr]
    second_answer = ClarificationAnswerSubmission(
        expected_intent_revision=second.intent_revision,
        answers=[
            ClarificationAnswerItem(
                slot="service",
                option_id=second.clarification.active_request().fields[0].options[0].id,  # type: ignore[union-attr]
            )
        ],
    )
    exhausted = apply_clarification_answer(
        second,
        second_answer,
        clarification_id=second.clarification.active_id or "",
        answered_by=str(uuid4()),
        answered_at=NOW + timedelta(minutes=3),
    ).intent
    with pytest.raises(ClarificationNoProgress, match="budget"):
        request_clarification(
            exhausted,
            question="third round",
            fields=[_field("repository")],
            requested_at=NOW + timedelta(minutes=4),
            expires_at=NOW + timedelta(hours=1),
            remaining_execution_seconds=169,
        )


def test_repeated_gap_fingerprint_fails_closed() -> None:
    first = _waiting_intent()
    option_id = first.clarification.active_request().fields[0].options[0].id  # type: ignore[union-attr]
    answered = apply_clarification_answer(
        first,
        ClarificationAnswerSubmission(
            expected_intent_revision=1,
            answers=[ClarificationAnswerItem(slot="repository", option_id=option_id)],
        ),
        clarification_id=first.clarification.active_id or "",
        answered_by=str(uuid4()),
        answered_at=NOW + timedelta(minutes=1),
    ).intent

    with pytest.raises(ClarificationNoProgress, match="same unresolved gap"):
        request_clarification(
            answered,
            question="different prose must not bypass the budget",
            fields=[
                _field(
                    options=[
                        make_option(label="renamed one", canonical_value="checkout-api"),
                        make_option(label="renamed two", canonical_value="payments-api"),
                    ]
                )
            ],
            requested_at=NOW + timedelta(minutes=2),
            expires_at=NOW + timedelta(hours=1),
            remaining_execution_seconds=171,
        )


def test_answer_item_requires_exactly_one_explicit_input() -> None:
    option_id = str(uuid4())
    assert ClarificationAnswerItem(slot="service", option_id=option_id).option_id == option_id
    assert ClarificationAnswerItem(slot="service", value="checkout").value == "checkout"

    with pytest.raises(PydanticValidationError):
        ClarificationAnswerItem(slot="service")
    with pytest.raises(PydanticValidationError):
        ClarificationAnswerItem(slot="service", option_id=option_id, value="checkout")
    with pytest.raises(PydanticValidationError):
        ClarificationAnswerItem(slot="service", option_id=None)
    with pytest.raises(PydanticValidationError):
        ClarificationAnswerItem(slot="service", value=None)


def test_submission_rejects_duplicate_slots_and_sorts_for_canonical_fingerprint() -> None:
    with pytest.raises(PydanticValidationError, match="unique"):
        ClarificationAnswerSubmission(
            expected_intent_revision=1,
            answers=[
                ClarificationAnswerItem(slot="service", value="checkout"),
                ClarificationAnswerItem(slot="service", value="payments"),
            ],
        )

    submission = ClarificationAnswerSubmission(
        expected_intent_revision=1,
        answers=[
            ClarificationAnswerItem(slot="time_range", value={"start": "s", "end": "e"}),
            ClarificationAnswerItem(slot="service", value="checkout"),
        ],
    )
    assert [item.slot for item in submission.answers] == ["service", "time_range"]


def test_answer_application_requires_exact_coverage_revision_and_declared_option() -> None:
    waiting = _waiting_intent([_field("repository"), _field("service")])
    active = waiting.clarification.active_request()
    assert active is not None

    with pytest.raises(ClarificationStaleRevision):
        apply_clarification_answer(
            waiting,
            ClarificationAnswerSubmission(
                expected_intent_revision=2,
                answers=[
                    ClarificationAnswerItem(
                        slot=field.slot,
                        option_id=field.options[0].id,
                    )
                    for field in active.fields
                ],
            ),
            clarification_id=active.id,
            answered_by=str(uuid4()),
            answered_at=NOW,
        )
    with pytest.raises(ClarificationAnswerInvalid, match="every requested"):
        apply_clarification_answer(
            waiting,
            ClarificationAnswerSubmission(
                expected_intent_revision=1,
                answers=[
                    ClarificationAnswerItem(
                        slot="repository",
                        option_id=active.fields[0].options[0].id,
                    )
                ],
            ),
            clarification_id=active.id,
            answered_by=str(uuid4()),
            answered_at=NOW,
        )
    with pytest.raises(ClarificationAnswerInvalid, match="unknown"):
        apply_clarification_answer(
            waiting,
            ClarificationAnswerSubmission(
                expected_intent_revision=1,
                answers=[
                    ClarificationAnswerItem(slot=field.slot, option_id=str(uuid4()))
                    for field in active.fields
                ],
            ),
            clarification_id=active.id,
            answered_by=str(uuid4()),
            answered_at=NOW,
        )


def test_answer_normalizes_redacts_and_merges_only_requested_slots() -> None:
    waiting = _waiting_intent(
        [
            _field("repository"),
            _field("time_range", options=[], allow_free_text=True, value_type="TIME_RANGE"),
        ]
    )
    active = waiting.clarification.active_request()
    assert active is not None
    repository_option = active.fields[0].options[0]
    original_route = waiting.route
    applied = apply_clarification_answer(
        waiting,
        ClarificationAnswerSubmission(
            expected_intent_revision=1,
            answers=[
                ClarificationAnswerItem(
                    slot="time_range",
                    value={
                        "start": "2026-09-04T09:00:00Z",
                        "end": "2026-09-04T10:00:00Z",
                    },
                ),
                ClarificationAnswerItem(
                    slot="repository",
                    option_id=repository_option.id,
                ),
            ],
        ),
        clarification_id=active.id,
        answered_by=str(uuid4()),
        answered_at=NOW + timedelta(minutes=1),
    )

    assert applied.intent.intent_revision == 2
    assert applied.intent.route == original_route
    assert applied.intent.clarification.active_id is None
    assert applied.intent.resolved_slot("repository").value == "checkout-api"  # type: ignore[union-attr]
    assert applied.answered_slots == ["repository", "time_range"]
    answered = applied.intent.clarification.requests[0]
    assert answered.status == "ANSWERED"
    assert answered.response_fingerprint == applied.response_fingerprint


def test_public_projection_is_positive_and_never_leaks_internal_resolution() -> None:
    secret_marker = "canonical-private-resource"
    field = _field(
        options=[
            make_option(label="checkout-api", canonical_value=secret_marker),
        ]
    )
    waiting = _waiting_intent([field])
    raw = waiting.model_dump(mode="json")

    public_intent = public_intent_projection(raw)
    pending = pending_clarification_projection(
        raw,
        run_id=str(uuid4()),
        status=RunStatus.WAITING_USER,
    )
    serialized = pending.model_dump_json() if pending is not None else ""

    assert set(public_intent) == {
        "schema_version",
        "intent_revision",
        "domain",
        "route",
        "intent",
        "question",
        "metrics",
        "dimensions",
        "time_range",
        "need_data",
        "need_root_cause",
        "risk",
        "agent",
        "skill",
        "decision",
        "decision_reason",
    }
    assert "clarification" not in public_intent
    assert pending is not None
    assert pending.gaps[0].options[0].label == "checkout-api"
    assert secret_marker not in serialized
    for forbidden in (
        "canonical_value",
        "value_fingerprint",
        "gap_fingerprint",
        "answers",
        "remaining_execution_seconds",
        "answered_by",
    ):
        assert forbidden not in serialized


def test_legacy_waiting_user_and_corrupt_typed_state_fail_closed() -> None:
    assert parse_run_intent({}, RunStatus.PENDING) is None
    assert public_intent_projection({"route": "KNOWLEDGE", "internal": "hidden"}) == {
        "route": "KNOWLEDGE"
    }
    with pytest.raises(ClarificationStateInvalid):
        parse_run_intent({}, RunStatus.WAITING_USER)

    corrupt = _waiting_intent().model_dump(mode="json")
    corrupt["clarification"]["active_id"] = None
    with pytest.raises(ClarificationStateInvalid):
        parse_run_intent(corrupt, RunStatus.WAITING_USER)


def test_fingerprints_are_stable_without_sorting_semantic_arrays() -> None:
    left = {"ordered": ["a", "b"], "map": {"z": 1, "a": 2}}
    same = {"map": {"a": 2, "z": 1}, "ordered": ["a", "b"]}
    different_order = {"map": {"z": 1, "a": 2}, "ordered": ["b", "a"]}

    assert fingerprint_value(left) == fingerprint_value(same)
    assert fingerprint_value(left) != fingerprint_value(different_order)


def test_persisted_state_detects_fingerprint_tampering() -> None:
    raw = deepcopy(_waiting_intent().model_dump(mode="json"))
    request = raw["clarification"]["requests"][0]
    request["fields"][0]["options"][0]["canonical_value"] = "tampered"

    with pytest.raises(ClarificationStateInvalid):
        parse_run_intent(raw, RunStatus.WAITING_USER)


def test_clarification_request_rejects_unknown_internal_fields() -> None:
    waiting = _waiting_intent()
    raw = waiting.clarification.requests[0].model_dump(mode="json")
    raw["unknown"] = True

    with pytest.raises(PydanticValidationError):
        ClarificationRequest.model_validate(raw)
