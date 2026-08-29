from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from obsion.domain.enums import RunFeedbackRating


class FeedbackModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RecordRunFeedbackRequest(FeedbackModel):
    rating: RunFeedbackRating
    reason: str = Field(default="", max_length=4_000)
    expected_version: int | None = Field(default=None, ge=1)


class RunFeedbackView(FeedbackModel):
    id: UUID
    run_id: UUID
    user_id: UUID
    rating: RunFeedbackRating
    reason: str
    version: int
    created_at: datetime
    updated_at: datetime


class FeedbackSummaryView(FeedbackModel):
    total: int
    helpful: int
    needs_improvement: int
    helpful_rate: float | None
