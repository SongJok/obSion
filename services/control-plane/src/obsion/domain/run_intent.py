import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from obsion.common.ids import new_id
from obsion.common.time import ensure_utc
from obsion.domain.enums import RunStatus
from obsion.security.redaction import redact, redact_text

INTENT_SCHEMA_VERSION: Literal[1] = 1
MAX_CLARIFICATION_ROUNDS = 2
MAX_CLARIFICATION_FIELDS = 3
MAX_CLARIFICATION_OPTIONS = 3
MAX_CLARIFICATION_VALUE_BYTES = 12_000

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_SENSITIVE_SLOT_PARTS = {
    "accesskey",
    "apikey",
    "authenticationheader",
    "authorization",
    "cookie",
    "credential",
    "dsn",
    "endpoint",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "token",
}
_DERIVED_SLOT_PARTS = {
    "agent",
    "clarification",
    "decision",
    "domain",
    "intent",
    "needdata",
    "needrootcause",
    "question",
    "risk",
    "route",
    "schemaversion",
    "skill",
}
_PUBLIC_INTENT_KEYS = (
    "schema_version",
    "intent_revision",
    "domain",
    "route",
    "intent",
    "question",
    "metrics",
    "dimensions",
    "time_range",
    "comparison",
    "need_data",
    "need_root_cause",
    "risk",
    "agent",
    "skill",
    "decision",
    "decision_reason",
)


class ClarificationDomainError(ValueError):
    """Base class for fail-closed clarification domain failures."""


class ClarificationStateInvalid(ClarificationDomainError):
    pass


class ClarificationNotPending(ClarificationDomainError):
    pass


class ClarificationStaleRevision(ClarificationDomainError):
    pass


class ClarificationAnswerInvalid(ClarificationDomainError):
    pass


class ClarificationNoProgress(ClarificationDomainError):
    pass


class StrictDomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ClarificationOption(StrictDomainModel):
    id: str
    label: str = Field(min_length=1, max_length=240)
    canonical_value: JsonValue
    value_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _canonical_uuid(value, "clarification option id")

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("clarification option label cannot be blank")
        return normalized

    @field_validator("canonical_value", mode="before")
    @classmethod
    def validate_canonical_value(cls, value: Any) -> Any:
        normalized = _normalize_json_value(value)
        _ensure_bounded_json(normalized)
        _reject_sensitive_mapping_keys(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_value_fingerprint(self) -> "ClarificationOption":
        if self.value_fingerprint != fingerprint_value(self.canonical_value):
            raise ValueError("clarification option fingerprint does not match its value")
        return self


class ClarificationField(StrictDomainModel):
    slot: str
    reason_code: str
    prompt: str = Field(min_length=1, max_length=1000)
    cardinality: Literal["ONE"] = "ONE"
    value_type: Literal["STRING", "TIME_RANGE"] = "STRING"
    options: list[ClarificationOption] = Field(
        default_factory=list,
        max_length=MAX_CLARIFICATION_OPTIONS,
    )
    allow_free_text: bool = False

    @field_validator("slot")
    @classmethod
    def validate_slot(cls, value: str) -> str:
        slot = _normalize_safe_id(value, "clarification slot")
        normalized = _collapsed_identifier(slot)
        if any(part in normalized for part in _SENSITIVE_SLOT_PARTS):
            raise ValueError("clarification cannot request credentials or private endpoints")
        if any(part in normalized for part in _DERIVED_SLOT_PARTS):
            raise ValueError("clarification cannot mutate derived intent fields")
        return slot

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        return _normalize_safe_id(value, "clarification reason code")

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("clarification prompt cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_input_contract(self) -> "ClarificationField":
        option_ids = [item.id for item in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("clarification option ids must be unique within a field")
        if not self.options and not self.allow_free_text:
            raise ValueError("a clarification field needs visible options or free-text input")
        return self


class StoredClarificationAnswer(StrictDomainModel):
    slot: str
    kind: Literal["OPTION", "VALUE"]
    value: JsonValue
    source_ref: str = Field(min_length=1, max_length=500)
    value_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("slot")
    @classmethod
    def validate_slot(cls, value: str) -> str:
        return _normalize_safe_id(value, "clarification answer slot")

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> Any:
        normalized = _normalize_json_value(value)
        _ensure_bounded_json(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_value_fingerprint(self) -> "StoredClarificationAnswer":
        if self.value_fingerprint != fingerprint_value(self.value):
            raise ValueError("clarification answer fingerprint does not match its value")
        return self


class ClarificationRequest(StrictDomainModel):
    id: str
    intent_revision: int = Field(ge=1)
    round: int = Field(ge=1, le=MAX_CLARIFICATION_ROUNDS)
    status: Literal["OPEN", "ANSWERED", "EXPIRED", "CANCELLED"]
    question: str = Field(min_length=1, max_length=2000)
    fields: list[ClarificationField] = Field(
        min_length=1,
        max_length=MAX_CLARIFICATION_FIELDS,
    )
    requested_at: str
    expires_at: str
    gap_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    answers: list[StoredClarificationAnswer] = Field(
        default_factory=list,
        max_length=MAX_CLARIFICATION_FIELDS,
    )
    answered_by: str | None = None
    answered_at: str | None = None
    response_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    closed_at: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _canonical_uuid(value, "clarification id")

    @field_validator("answered_by")
    @classmethod
    def validate_answered_by(cls, value: str | None) -> str | None:
        return _canonical_uuid(value, "answering principal id") if value is not None else None

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("clarification question cannot be blank")
        return normalized

    @field_validator("requested_at", "expires_at", "answered_at", "closed_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_timestamp(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ClarificationRequest":
        slots = [item.slot for item in self.fields]
        if len(slots) != len(set(slots)):
            raise ValueError("clarification field slots must be unique")
        if ensure_utc(datetime.fromisoformat(self.expires_at)) <= ensure_utc(
            datetime.fromisoformat(self.requested_at)
        ):
            raise ValueError("clarification expiry must be after its request time")
        answer_slots = [item.slot for item in self.answers]
        if len(answer_slots) != len(set(answer_slots)):
            raise ValueError("stored clarification answer slots must be unique")
        if self.status == "OPEN":
            if self.answers or any(
                value is not None
                for value in (
                    self.answered_by,
                    self.answered_at,
                    self.response_fingerprint,
                    self.closed_at,
                )
            ):
                raise ValueError("an open clarification cannot contain closure metadata")
        elif self.status == "ANSWERED":
            if set(answer_slots) != set(slots):
                raise ValueError("an answered clarification must contain every requested field")
            if (
                self.answered_by is None
                or self.answered_at is None
                or self.response_fingerprint is None
                or self.closed_at is not None
            ):
                raise ValueError("answered clarification metadata is incomplete")
        elif self.status in {"EXPIRED", "CANCELLED"}:
            if self.answers or any(
                value is not None
                for value in (
                    self.answered_by,
                    self.answered_at,
                    self.response_fingerprint,
                )
            ):
                raise ValueError("an unanswered closed clarification cannot contain answers")
            if self.closed_at is None:
                raise ValueError("a closed clarification requires its closure time")
        if self.gap_fingerprint != fingerprint_gap(self.fields):
            raise ValueError("clarification gap fingerprint does not match its fields")
        if self.request_fingerprint != fingerprint_request(self):
            raise ValueError("clarification request fingerprint does not match its public contract")
        return self


class ClarificationState(StrictDomainModel):
    active_id: str | None = None
    requests: list[ClarificationRequest] = Field(
        default_factory=list,
        max_length=MAX_CLARIFICATION_ROUNDS,
    )
    remaining_execution_seconds: int | None = Field(default=None, ge=1)

    @field_validator("active_id")
    @classmethod
    def validate_active_id(cls, value: str | None) -> str | None:
        return _canonical_uuid(value, "active clarification id") if value is not None else None

    @model_validator(mode="after")
    def validate_active_request(self) -> "ClarificationState":
        ids = [item.id for item in self.requests]
        rounds = [item.round for item in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("clarification request ids must be unique")
        if rounds != list(range(1, len(rounds) + 1)):
            raise ValueError("clarification rounds must be contiguous and ordered")
        open_requests = [item for item in self.requests if item.status == "OPEN"]
        if len(open_requests) > 1:
            raise ValueError("a run can have at most one open clarification")
        if open_requests:
            if self.active_id != open_requests[0].id or self.requests[-1].id != self.active_id:
                raise ValueError("the active clarification must be the final open request")
            if self.remaining_execution_seconds is None:
                raise ValueError("an active clarification must preserve execution time")
            if any(item.status != "ANSWERED" for item in self.requests[:-1]):
                raise ValueError("only answered requests may precede an active clarification")
        elif self.active_id is not None or self.remaining_execution_seconds is not None:
            raise ValueError("inactive clarification state cannot retain an active id or deadline")
        return self

    def active_request(self) -> ClarificationRequest | None:
        if self.active_id is None:
            return None
        return next((item for item in self.requests if item.id == self.active_id), None)


class ResolvedSlot(StrictDomainModel):
    slot: str
    value: JsonValue
    source: Literal[
        "INPUT",
        "CONTEXT_REF",
        "CONVERSATION",
        "MEMORY",
        "WORKSPACE",
        "CATALOG",
        "MANIFEST",
        "CAPABILITY",
        "CLARIFICATION_OPTION",
        "CLARIFICATION_VALUE",
    ]
    source_ref: str = Field(min_length=1, max_length=500)
    confidence_rank: int = Field(ge=1, le=100)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("slot")
    @classmethod
    def validate_slot(cls, value: str) -> str:
        return _normalize_safe_id(value, "resolved slot")

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> Any:
        normalized = _normalize_json_value(value)
        _ensure_bounded_json(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_fingerprint(self) -> "ResolvedSlot":
        expected = fingerprint_value(
            {
                "slot": self.slot,
                "value": self.value,
                "source": self.source,
                "source_ref": self.source_ref,
                "confidence_rank": self.confidence_rank,
            }
        )
        if self.fingerprint != expected:
            raise ValueError("resolved slot fingerprint does not match its provenance")
        return self


class RunIntent(StrictDomainModel):
    schema_version: Literal[1] = INTENT_SCHEMA_VERSION
    intent_revision: int = Field(default=1, ge=1)
    preparation_stage: Literal[
        "CONTEXT_RESOLVED",
        "WAITING_USER",
        "INTENT_RESOLVED",
        "PLANNED",
    ]
    domain: str = Field(min_length=1, max_length=80)
    route: str = Field(min_length=1, max_length=80)
    intent: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=100_000)
    metrics: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=100)
    dimensions: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=100)
    time_range: dict[str, str] = Field(default_factory=dict)
    comparison: str | None = Field(default=None, max_length=120)
    need_data: bool = False
    need_root_cause: bool = False
    risk: str = Field(default="L1", pattern=r"^L[0-5]$")
    agent: str | None = Field(default=None, max_length=160)
    skill: str | None = Field(default=None, max_length=160)
    skill_version: int | None = Field(default=None, ge=1)
    skill_checksum_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decision: Literal["PROCEED", "CLARIFY", "UNAVAILABLE", "DENY"] | None = None
    decision_reason: str | None = Field(default=None, max_length=200)
    resolved_slots: list[ResolvedSlot] = Field(default_factory=list, max_length=20)
    clarification: ClarificationState = Field(default_factory=ClarificationState)

    @field_validator("domain", "route", "intent", "risk", "agent", "skill")
    @classmethod
    def normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized:
            raise ValueError("intent identifiers cannot be blank")
        return normalized

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = redact_text(unicodedata.normalize("NFKC", value).strip())
        if not normalized:
            raise ValueError("intent question cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_state(self) -> "RunIntent":
        slot_names = [item.slot for item in self.resolved_slots]
        if len(slot_names) != len(set(slot_names)):
            raise ValueError("resolved intent slots must be unique")
        active = self.clarification.active_request()
        if self.preparation_stage == "WAITING_USER":
            if self.decision != "CLARIFY" or active is None:
                raise ValueError("WAITING_USER intent requires one active clarification")
            if active.intent_revision != self.intent_revision:
                raise ValueError("active clarification must target the current intent revision")
        elif active is not None:
            raise ValueError("only WAITING_USER intent may contain an active clarification")
        if self.decision == "CLARIFY" and self.preparation_stage != "WAITING_USER":
            raise ValueError("CLARIFY decision requires WAITING_USER preparation stage")
        if self.preparation_stage == "PLANNED" and self.decision != "PROCEED":
            raise ValueError("a planned intent must have a PROCEED decision")
        if (self.skill_version is None) != (self.skill_checksum_sha256 is None):
            raise ValueError("skill version and checksum must be pinned together")
        if self.skill is None and self.skill_version is not None:
            raise ValueError("a skill pin requires a skill name")
        return self

    def resolved_slot(self, slot: str) -> ResolvedSlot | None:
        normalized = _normalize_safe_id(slot, "resolved slot")
        return next((item for item in self.resolved_slots if item.slot == normalized), None)


class ClarificationAnswerItem(StrictDomainModel):
    slot: str
    option_id: str | None = None
    value: JsonValue | None = None

    @field_validator("slot")
    @classmethod
    def validate_slot(cls, value: str) -> str:
        return _normalize_safe_id(value, "clarification answer slot")

    @field_validator("option_id")
    @classmethod
    def validate_option_id(cls, value: str | None) -> str | None:
        return _canonical_uuid(value, "clarification option id") if value is not None else None

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized = _normalize_json_value(value)
        _ensure_bounded_json(normalized)
        return normalized

    @model_validator(mode="after")
    def validate_exactly_one(self) -> "ClarificationAnswerItem":
        supplied = self.model_fields_set.intersection({"option_id", "value"})
        if len(supplied) != 1:
            raise ValueError("answer must contain exactly one of option_id or value")
        if "option_id" in supplied and self.option_id is None:
            raise ValueError("option_id cannot be null")
        if "value" in supplied and self.value is None:
            raise ValueError("answer value cannot be null")
        return self


class ClarificationAnswerSubmission(StrictDomainModel):
    expected_intent_revision: int = Field(ge=1)
    answers: list[ClarificationAnswerItem] = Field(
        min_length=1,
        max_length=MAX_CLARIFICATION_FIELDS,
    )

    @model_validator(mode="after")
    def validate_answers(self) -> "ClarificationAnswerSubmission":
        slots = [item.slot for item in self.answers]
        if len(slots) != len(set(slots)):
            raise ValueError("clarification answer slots must be unique")
        self.answers = sorted(self.answers, key=lambda item: item.slot)
        return self


class PublicClarificationOption(StrictDomainModel):
    id: str
    label: str


class PublicClarificationField(StrictDomainModel):
    slot: str
    reason_code: str
    prompt: str
    cardinality: Literal["ONE"]
    value_type: Literal["STRING", "TIME_RANGE"]
    options: list[PublicClarificationOption]
    allow_free_text: bool


class PendingClarification(StrictDomainModel):
    id: str
    run_id: str
    intent_revision: int
    round: int
    status: Literal["OPEN"]
    question: str
    gaps: list[PublicClarificationField]
    requested_at: str
    expires_at: str


class AppliedClarificationAnswer(StrictDomainModel):
    intent: RunIntent
    clarification_id: str
    answered_slots: list[str]
    response_fingerprint: str


def fingerprint_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def fingerprint_gap(fields: list[ClarificationField]) -> str:
    payload = [
        {
            "slot": field.slot,
            "reason_code": field.reason_code,
            "cardinality": field.cardinality,
            "value_type": field.value_type,
            "allow_free_text": field.allow_free_text,
            "options": sorted(item.value_fingerprint for item in field.options),
        }
        for field in sorted(fields, key=lambda item: item.slot)
    ]
    return fingerprint_value(payload)


def fingerprint_request(request: ClarificationRequest) -> str:
    payload = {
        "id": request.id,
        "intent_revision": request.intent_revision,
        "round": request.round,
        "question": request.question,
        "fields": [
            {
                "slot": field.slot,
                "reason_code": field.reason_code,
                "prompt": field.prompt,
                "cardinality": field.cardinality,
                "value_type": field.value_type,
                "allow_free_text": field.allow_free_text,
                "options": [{"id": option.id, "label": option.label} for option in field.options],
            }
            for field in sorted(request.fields, key=lambda item: item.slot)
        ],
        "requested_at": request.requested_at,
        "expires_at": request.expires_at,
    }
    return fingerprint_value(payload)


def make_option(*, label: str, canonical_value: JsonValue) -> ClarificationOption:
    normalized = _normalize_json_value(canonical_value)
    return ClarificationOption(
        id=str(new_id()),
        label=label,
        canonical_value=normalized,
        value_fingerprint=fingerprint_value(normalized),
    )


def make_resolved_slot(
    *,
    slot: str,
    value: JsonValue,
    source: Literal[
        "INPUT",
        "CONTEXT_REF",
        "CONVERSATION",
        "MEMORY",
        "WORKSPACE",
        "CATALOG",
        "MANIFEST",
        "CAPABILITY",
        "CLARIFICATION_OPTION",
        "CLARIFICATION_VALUE",
    ],
    source_ref: str,
    confidence_rank: int,
) -> ResolvedSlot:
    normalized = _normalize_json_value(value)
    normalized_slot = _normalize_safe_id(slot, "resolved slot")
    payload = {
        "slot": normalized_slot,
        "value": normalized,
        "source": source,
        "source_ref": source_ref,
        "confidence_rank": confidence_rank,
    }
    return ResolvedSlot(**payload, fingerprint=fingerprint_value(payload))


def request_clarification(
    intent: RunIntent,
    *,
    question: str,
    fields: list[ClarificationField],
    requested_at: datetime,
    expires_at: datetime,
    remaining_execution_seconds: int,
) -> RunIntent:
    if intent.clarification.active_request() is not None:
        raise ClarificationStateInvalid("the run already has an open clarification")
    if len(intent.clarification.requests) >= MAX_CLARIFICATION_ROUNDS:
        raise ClarificationNoProgress("the clarification round budget is exhausted")
    gap_fingerprint = fingerprint_gap(fields)
    if any(item.gap_fingerprint == gap_fingerprint for item in intent.clarification.requests):
        raise ClarificationNoProgress("clarification repeated the same unresolved gap")
    request_id = str(new_id())
    round_number = len(intent.clarification.requests) + 1
    draft = ClarificationRequest.model_construct(
        id=request_id,
        intent_revision=intent.intent_revision,
        round=round_number,
        status="OPEN",
        question=redact_text(unicodedata.normalize("NFKC", question).strip()),
        fields=fields,
        requested_at=_timestamp(requested_at),
        expires_at=_timestamp(expires_at),
        gap_fingerprint=gap_fingerprint,
        request_fingerprint="",
        answers=[],
        answered_by=None,
        answered_at=None,
        response_fingerprint=None,
        closed_at=None,
    )
    request = draft.model_copy(update={"request_fingerprint": fingerprint_request(draft)})
    request = ClarificationRequest.model_validate(request.model_dump(mode="python"))
    clarification = ClarificationState(
        active_id=request.id,
        requests=[*intent.clarification.requests, request],
        remaining_execution_seconds=remaining_execution_seconds,
    )
    return intent.model_copy(
        update={
            "preparation_stage": "WAITING_USER",
            "decision": "CLARIFY",
            "decision_reason": "blocking_information_required",
            "clarification": clarification,
        }
    )


def apply_clarification_answer(
    intent: RunIntent,
    submission: ClarificationAnswerSubmission,
    *,
    clarification_id: str,
    answered_by: str,
    answered_at: datetime,
) -> AppliedClarificationAnswer:
    active = intent.clarification.active_request()
    canonical_id = _canonical_uuid(clarification_id, "clarification id")
    if active is None or active.id != canonical_id or active.status != "OPEN":
        raise ClarificationNotPending("the clarification is not the active open request")
    if submission.expected_intent_revision != intent.intent_revision:
        raise ClarificationStaleRevision("the clarification targets a stale intent revision")
    requested = {field.slot: field for field in active.fields}
    submitted = {item.slot: item for item in submission.answers}
    if set(submitted) != set(requested):
        raise ClarificationAnswerInvalid("answers must match every requested clarification field")

    stored_answers: list[StoredClarificationAnswer] = []
    resolved: list[ResolvedSlot] = []
    for slot in sorted(requested):
        field = requested[slot]
        answer = submitted[slot]
        if answer.option_id is not None:
            option = next(
                (item for item in field.options if item.id == answer.option_id),
                None,
            )
            if option is None:
                raise ClarificationAnswerInvalid(
                    "answer references an unknown clarification option"
                )
            value = option.canonical_value
            kind: Literal["OPTION", "VALUE"] = "OPTION"
            source: Literal["CLARIFICATION_OPTION", "CLARIFICATION_VALUE"] = "CLARIFICATION_OPTION"
            source_ref = option.id
        else:
            if not field.allow_free_text:
                raise ClarificationAnswerInvalid("free-text input is not allowed for this field")
            value = answer.value
            _validate_free_text_value(field, value)
            kind = "VALUE"
            source = "CLARIFICATION_VALUE"
            source_ref = active.id
        value_fingerprint = fingerprint_value(value)
        stored_answers.append(
            StoredClarificationAnswer(
                slot=slot,
                kind=kind,
                value=value,
                source_ref=source_ref,
                value_fingerprint=value_fingerprint,
            )
        )
        resolved.append(
            make_resolved_slot(
                slot=slot,
                value=value,
                source=source,
                source_ref=source_ref,
                confidence_rank=100,
            )
        )

    response_fingerprint = fingerprint_value(
        [
            {"slot": item.slot, "kind": item.kind, "value_fingerprint": item.value_fingerprint}
            for item in stored_answers
        ]
    )
    answered_request = active.model_copy(
        update={
            "status": "ANSWERED",
            "answers": stored_answers,
            "answered_by": _canonical_uuid(answered_by, "answering principal id"),
            "answered_at": _timestamp(answered_at),
            "response_fingerprint": response_fingerprint,
        }
    )
    answered_request = ClarificationRequest.model_validate(
        answered_request.model_dump(mode="python")
    )
    requests = [
        answered_request if item.id == active.id else item for item in intent.clarification.requests
    ]
    resolved_by_slot = {item.slot: item for item in intent.resolved_slots}
    resolved_by_slot.update({item.slot: item for item in resolved})
    updated_intent = intent.model_copy(
        update={
            "intent_revision": intent.intent_revision + 1,
            "preparation_stage": "CONTEXT_RESOLVED",
            "decision": None,
            "decision_reason": None,
            "resolved_slots": [resolved_by_slot[key] for key in sorted(resolved_by_slot)],
            "clarification": ClarificationState(requests=requests),
        }
    )
    updated_intent = RunIntent.model_validate(updated_intent.model_dump(mode="python"))
    return AppliedClarificationAnswer(
        intent=updated_intent,
        clarification_id=active.id,
        answered_slots=sorted(submitted),
        response_fingerprint=response_fingerprint,
    )


def close_active_clarification(
    intent: RunIntent,
    *,
    status: Literal["EXPIRED", "CANCELLED"],
    closed_at: datetime,
) -> RunIntent:
    active = intent.clarification.active_request()
    if active is None:
        return intent
    closed = active.model_copy(update={"status": status, "closed_at": _timestamp(closed_at)})
    closed = ClarificationRequest.model_validate(closed.model_dump(mode="python"))
    requests = [closed if item.id == active.id else item for item in intent.clarification.requests]
    return intent.model_copy(
        update={
            "preparation_stage": "INTENT_RESOLVED",
            "decision": "UNAVAILABLE",
            "decision_reason": (
                "clarification_expired" if status == "EXPIRED" else "clarification_cancelled"
            ),
            "clarification": ClarificationState(requests=requests),
        }
    )


def parse_run_intent(raw: Any, status: RunStatus | str) -> RunIntent | None:
    waiting = str(status) == RunStatus.WAITING_USER.value
    if not isinstance(raw, dict):
        if waiting:
            raise ClarificationStateInvalid("WAITING_USER run has no typed intent state")
        return None
    if raw.get("schema_version") != INTENT_SCHEMA_VERSION:
        if waiting:
            raise ClarificationStateInvalid("legacy WAITING_USER intent cannot be resumed")
        return None
    try:
        parsed = RunIntent.model_validate(raw)
    except (TypeError, ValueError) as exc:
        raise ClarificationStateInvalid("run intent state is invalid") from exc
    if waiting and parsed.preparation_stage != "WAITING_USER":
        raise ClarificationStateInvalid("WAITING_USER run has no active clarification")
    return parsed


def public_intent_projection(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and raw.get("schema_version") == INTENT_SCHEMA_VERSION:
        try:
            intent = RunIntent.model_validate(raw)
        except (TypeError, ValueError):
            return {}
        dumped = intent.model_dump(mode="json")
        return {
            key: dumped[key]
            for key in _PUBLIC_INTENT_KEYS
            if key in dumped and dumped[key] is not None
        }
    if not isinstance(raw, dict):
        return {}
    safe = {
        key: raw[key]
        for key in _PUBLIC_INTENT_KEYS
        if key in raw and key not in {"schema_version", "intent_revision"}
    }
    projected = redact(safe)
    return projected if isinstance(projected, dict) else {}


def pending_clarification_projection(
    raw: Any,
    *,
    run_id: str,
    status: RunStatus | str,
) -> PendingClarification | None:
    if str(status) != RunStatus.WAITING_USER.value:
        return None
    intent = parse_run_intent(raw, status)
    if intent is None:
        raise ClarificationStateInvalid("WAITING_USER run has no typed intent state")
    active = intent.clarification.active_request()
    if active is None:
        raise ClarificationStateInvalid("WAITING_USER run has no active clarification")
    return PendingClarification(
        id=active.id,
        run_id=_canonical_uuid(run_id, "run id"),
        intent_revision=intent.intent_revision,
        round=active.round,
        status="OPEN",
        question=active.question,
        gaps=[
            PublicClarificationField(
                slot=field.slot,
                reason_code=field.reason_code,
                prompt=field.prompt,
                cardinality=field.cardinality,
                value_type=field.value_type,
                options=[
                    PublicClarificationOption(id=option.id, label=option.label)
                    for option in field.options
                ],
                allow_free_text=field.allow_free_text,
            )
            for field in active.fields
        ],
        requested_at=active.requested_at,
        expires_at=active.expires_at,
    )


def _validate_free_text_value(field: ClarificationField, value: JsonValue | None) -> None:
    if field.value_type == "STRING":
        if not isinstance(value, str) or not value:
            raise ClarificationAnswerInvalid("clarification field requires a non-empty string")
        return
    if not isinstance(value, dict):
        raise ClarificationAnswerInvalid("time range clarification requires an object")
    if set(value) - {"start", "end", "timezone"} or not {"start", "end"}.issubset(value):
        raise ClarificationAnswerInvalid("time range clarification has unsupported fields")
    start = value.get("start")
    end = value.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ClarificationAnswerInvalid("time range boundaries must be strings")
    try:
        start_at = ensure_utc(datetime.fromisoformat(start))
        end_at = ensure_utc(datetime.fromisoformat(end))
    except ValueError as exc:
        raise ClarificationAnswerInvalid("time range boundaries must be ISO-8601") from exc
    if start_at >= end_at:
        raise ClarificationAnswerInvalid("time range start must be before its end")


def _canonical_uuid(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ValueError(f"{label} must use canonical UUID form")
    return canonical


def _canonical_timestamp(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("clarification timestamp must be a string")
    try:
        parsed = ensure_utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise ValueError("clarification timestamp must be ISO-8601") from exc
    return parsed.isoformat()


def _timestamp(value: datetime) -> str:
    return ensure_utc(value).isoformat()


def _normalize_safe_id(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not _SAFE_ID.fullmatch(normalized):
        raise ValueError(f"{label} contains unsupported characters")
    return normalized


def _collapsed_identifier(value: str) -> str:
    return "".join(character for character in value if character.isalnum()).casefold()


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(unicodedata.normalize("NFKC", value).strip())
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("clarification values must be finite JSON")
        return value
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    raise ValueError("clarification values must be JSON-compatible")


def _reject_sensitive_mapping_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _collapsed_identifier(str(key))
            if any(part in normalized for part in _SENSITIVE_SLOT_PARTS):
                raise ValueError("clarification option values cannot contain secret fields")
            _reject_sensitive_mapping_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive_mapping_keys(item)


def _ensure_bounded_json(value: Any) -> None:
    if len(_canonical_json(value).encode()) > MAX_CLARIFICATION_VALUE_BYTES:
        raise ValueError("clarification value exceeds the bounded size")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be canonical JSON") from exc
