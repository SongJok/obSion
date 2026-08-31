from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from obsion.model_gateway.context import ContextSegment, TrustLevel

KEEP_RECENT = 2
PREVIEW_CHARS = 120
SUMMARY_BUDGET = 800


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    ordinal: int
    source_turn_id: UUID
    source_principal_id: UUID
    user_content: str
    assistant_content: str | None


@dataclass(frozen=True, slots=True)
class CompactedHistory:
    recent: tuple[ConversationTurn, ...]
    summary_segment: ContextSegment | None
    method: str
    keep_recent: int
    kept_turns: int
    summarized_turns: int
    summarized_turn_ids: tuple[str, ...]
    summary: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "keep_recent": self.keep_recent,
            "kept_turns": self.kept_turns,
            "summarized_turns": self.summarized_turns,
            "source_turn_ids": list(self.summarized_turn_ids),
            "summary": self.summary,
        }


class ConversationCompactor:
    """Extractive conversation compaction. This is not a model summarize path."""

    def __init__(
        self,
        *,
        keep_recent: int = KEEP_RECENT,
        preview_chars: int = PREVIEW_CHARS,
        summary_budget: int = SUMMARY_BUDGET,
    ) -> None:
        self.keep_recent = max(1, keep_recent)
        self.preview_chars = max(16, preview_chars)
        self.summary_budget = max(64, summary_budget)

    def compact(self, turns: Sequence[ConversationTurn]) -> CompactedHistory:
        ordered = tuple(turns)
        if len(ordered) <= self.keep_recent:
            return CompactedHistory(
                recent=ordered,
                summary_segment=None,
                method="extractive",
                keep_recent=self.keep_recent,
                kept_turns=len(ordered),
                summarized_turns=0,
                summarized_turn_ids=(),
                summary=None,
            )
        older = ordered[: -self.keep_recent]
        recent = ordered[-self.keep_recent :]
        items = [_preview(item, self.preview_chars) for item in older]
        payload: dict[str, Any] = {
            "summarized": True,
            "method": "extractive",
            "count": len(older),
            "turns": items,
        }
        text = _dump(payload)
        while len(text) > self.summary_budget and items:
            items.pop(0)
            payload["turns"] = items
            payload["dropped_items"] = True
            text = _dump(payload)
        text = text[: self.summary_budget]
        return CompactedHistory(
            recent=recent,
            summary_segment=ContextSegment(
                TrustLevel.UNTRUSTED_DATA,
                text,
                "conversation-compact",
                550,
                290,
            ),
            method="extractive",
            keep_recent=self.keep_recent,
            kept_turns=len(recent),
            summarized_turns=len(older),
            summarized_turn_ids=tuple(str(item.source_turn_id) for item in older),
            summary=payload,
        )


def conversation_turns_from_snapshots(snapshots: Sequence[Any]) -> tuple[ConversationTurn, ...]:
    return tuple(
        ConversationTurn(
            ordinal=int(item.ordinal),
            source_turn_id=item.source_turn_id,
            source_principal_id=item.source_principal_id,
            user_content=item.user_content,
            assistant_content=item.assistant_content,
        )
        for item in snapshots
    )


def _preview(item: ConversationTurn, limit: int) -> dict[str, Any]:
    assistant = item.assistant_content or ""
    return {
        "ordinal": item.ordinal,
        "source_turn_id": str(item.source_turn_id),
        "user": item.user_content[:limit],
        "assistant": assistant[:limit] if assistant else None,
    }


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
