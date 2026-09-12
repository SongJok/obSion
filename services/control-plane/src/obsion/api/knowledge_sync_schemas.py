"""Metadata-only managed source contract; no credentials or read-result input."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RegisterKnowledgeSource(SourceModel):
    connector_id: UUID
    binding_id: UUID


class ControlKnowledgeSource(SourceModel):
    operation: Literal["pause", "resume", "sync"]


class KnowledgeSourceConnection(SourceModel):
    connector_id: UUID
    binding_id: UUID
    name: str
    corp_id: str


class KnowledgeSourceCounts(SourceModel):
    files: int = 0
    available: int = 0
    pending: int = 0
    partial: int = 0
    failed: int = 0
    removed: int = 0
    containers: int = 0
    awaiting_access_check: int = 0


class KnowledgeSourceView(SourceModel):
    id: UUID
    connector_id: UUID
    name: str
    corp_id: str
    active: bool
    state: Literal["PAUSED", "QUEUED", "SYNCING", "ATTENTION", "CURRENT"]
    counts: KnowledgeSourceCounts
    last_scan_completed_at: datetime | None
    next_check_at: datetime
    issue: str | None = None


class KnowledgeSourcePage(SourceModel):
    items: list[KnowledgeSourceView]
    next_cursor: UUID | None = None


class KnowledgeSourceItemView(SourceModel):
    id: UUID
    title: str
    kind: str
    status: str
    available: bool
    document_id: UUID | None
    revision: str | None
    checked_at: datetime | None
    issues: list[str] = Field(default_factory=list)


class KnowledgeSourceItemPage(SourceModel):
    items: list[KnowledgeSourceItemView]
    next_cursor: UUID | None = None
