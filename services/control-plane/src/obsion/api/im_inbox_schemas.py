from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateImInstallation(ImInput):
    provider: Literal["dingtalk", "feishu", "wecom"]
    external_corp_id: str = Field(min_length=1, max_length=255)
    external_app_id: str = Field(min_length=1, max_length=255)
    connector_id: UUID
    adapter_principal_id: UUID
    verification_source: str = Field(min_length=1, max_length=255)


class CreateInstallationBinding(ImInput):
    sender_id: str = Field(min_length=1, max_length=255)
    user_id: UUID


class CreateImGroupAudience(ImInput):
    conversation_id: str = Field(min_length=1, max_length=512)
    workspace_id: UUID
    member_user_ids: list[UUID] = Field(min_length=1, max_length=500)
    member_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    max_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = "INTERNAL"
    allow_final_answer: bool = False
    allow_status: bool = True
    verification_source: str = Field(min_length=1, max_length=255)

    @field_validator("member_user_ids")
    @classmethod
    def unique_member_ids(cls, values: list[UUID]) -> list[UUID]:
        unique = list(dict.fromkeys(values))
        if not unique:
            raise ValueError("at least one group member is required")
        return unique


class TrustedImInbound(ImInput):
    """Normalized envelope only; installation and actor come from trusted routing/AuthN."""

    vendor_event_id: str = Field(min_length=1, max_length=255)
    sender_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(min_length=1, max_length=512)
    conversation_type: Literal["direct", "group"]
    text: str = Field(min_length=1, max_length=32000)


class ImInstallationView(CreateImInstallation):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    status: str
    created_by: UUID
    created_at: datetime
    revoked_at: datetime | None


class ImInstallationBindingView(CreateInstallationBinding):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    installation_id: UUID
    active: bool
    revoked_at: datetime | None


class ImGroupAudienceView(CreateImGroupAudience):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    installation_id: UUID
    status: str
    created_by: UUID
    verified_at: datetime
    verified_until: datetime
    revoked_at: datetime | None


class ImInboxView(BaseModel):
    """Content-free durable receipt; acceptance is not execution or vendor delivery."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    installation_id: UUID
    vendor_event_id: str
    status: str
    turn_id: UUID | None
    run_id: UUID | None
    created_at: datetime
    processed_at: datetime | None

    @field_validator("created_at", "processed_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class DingTalkOutboxView(BaseModel):
    """Content-free operator projection of a durable robot delivery."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    installation_id: UUID
    inbox_message_id: UUID
    run_id: UUID
    recipient_user_id: UUID
    recipient_sender_id: str
    conversation_type: str
    recipient_conversation_id: str | None
    audience_id: UUID | None
    audience_member_fingerprint: str | None
    delivery_mode: str
    answer_classification: str | None
    connector_id: UUID
    capability_version_id: UUID | None
    status: str
    attempt_count: int
    fencing_token: int
    next_attempt_at: datetime | None
    lease_expires_at: datetime | None
    accepted_process_query_key: str | None
    vendor_send_status: str | None
    vendor_read_status: str | None
    vendor_read_at: datetime | None
    last_reconciled_at: datetime | None
    reconciliation_attempt_count: int
    blocked_reason: str | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
