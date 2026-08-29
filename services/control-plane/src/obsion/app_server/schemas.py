from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from obsion.domain.enums import ApprovalStatus


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InitializeParams(Params):
    protocol_version: str
    client_name: str = Field(min_length=1, max_length=120)
    client_version: str = Field(min_length=1, max_length=80)
    bearer_token: str | None = Field(default=None, min_length=1, max_length=16_384)


class EmptyParams(Params):
    pass


class MutationParams(Params):
    client_request_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class WorkspaceListParams(Params):
    include_archived: bool = False


class ThreadListParams(Params):
    workspace_id: UUID
    include_archived: bool = False


class ThreadCreateParams(MutationParams):
    workspace_id: UUID
    title: str = Field(min_length=1, max_length=300)


class ThreadMutationParams(MutationParams):
    thread_id: UUID


class ThreadForkParams(ThreadMutationParams):
    from_turn_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class ThreadReadParams(Params):
    thread_id: UUID


class ThreadEventsParams(ThreadReadParams):
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=500)


class TurnCreateParams(MutationParams):
    thread_id: UUID
    input: str = Field(min_length=1, max_length=100_000)
    context_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    attachment_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    model_profile: str | None = Field(default=None, max_length=120)


class RunReadParams(Params):
    run_id: UUID


class RunMutationParams(MutationParams):
    run_id: UUID


class RunEventsParams(RunReadParams):
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2000)


class RunSubscribeParams(RunReadParams):
    after_sequence: int = Field(default=0, ge=0)


class RunUnsubscribeParams(Params):
    subscription_id: str = Field(min_length=1, max_length=100)


class ApprovalListParams(Params):
    status: ApprovalStatus | None = None


class ApprovalDecideParams(MutationParams):
    approval_id: UUID
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=4000)


class ArtifactListParams(Params):
    workspace_id: UUID


class ArtifactGetParams(Params):
    artifact_id: UUID
