"""User-authored task scope and presentation preferences, never authorization."""

import json
import re
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from obsion.security.redaction import redact_text

OutputFormat = Literal["AUTO", "TABLE", "BULLETS", "REPORT"]
TASK_CONTEXT_SLOTS = frozenset({"selected_documents", "output_format", "task_constraints"})


class TaskContextReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["task_context"]
    document_ids: list[str] = Field(default_factory=list, max_length=4)
    output_format: OutputFormat = "AUTO"
    constraints: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("document_ids")
    @classmethod
    def canonical_documents(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(str(UUID(item)) != item for item in value):
            raise ValueError("Selected documents must be unique canonical UUIDs")
        return value

    @field_validator("constraints")
    @classmethod
    def bounded_constraints(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("Task constraints must contain 1 to 1000 characters each")
        return [redact_text(item.strip()) for item in value]


def task_context_reference(references: list[dict[str, Any]]) -> TaskContextReference | None:
    matches = [item for item in references if item.get("type") == "task_context"]
    if len(matches) > 1:
        raise ValueError("Only one task_context reference is permitted")
    return TaskContextReference.model_validate(matches[0]) if matches else None


def requested_format(question: str) -> OutputFormat | None:
    # Only a complete formatting request changes a persistent preference.
    # Mentions in document quotations or a new topic are not user selections.
    prefix = r"(?:(?:请|麻烦|能否|可以|帮我|再|继续|把它|把上面的内容|对此|那)[，,\s]*)*"
    suffix = r"(?:形式)?(?:说明|解释|回答|总结|展示|呈现|输出)?[。？！!?,，\s]*"
    if re.fullmatch(
        prefix + r"(?:(?:不要|别|不必)(?:再)?(?:用|使用)|不用)(?:表格|列表|报告|段落)" + suffix,
        question.strip(),
    ):
        return "AUTO"
    match = re.fullmatch(
        prefix + r"(?:用|换成|改成|以)(表格|列表|报告|段落)" + suffix,
        question.strip(),
    )
    if match is None:
        return None
    formats: dict[str, OutputFormat] = {
        "表格": "TABLE",
        "列表": "BULLETS",
        "报告": "REPORT",
        "段落": "AUTO",
    }
    return formats[match[1]]


def presentation_preferences(intent: dict[str, Any]) -> dict[str, Any]:
    return {
        item["slot"]: item["value"]
        for item in intent.get("resolved_slots", [])
        if isinstance(item, dict) and item.get("slot") in {"output_format", "task_constraints"}
    }


def selected_document_scope(intent: dict[str, Any]) -> list[dict[str, Any]]:
    for item in intent.get("resolved_slots", []):
        if item.get("slot") == "selected_documents":
            return cast(list[dict[str, Any]], item["value"])
    return []


def task_prompt(intent: dict[str, Any], fallback: str) -> str:
    question = str(intent.get("question") or fallback)
    preferences = presentation_preferences(intent)
    if not preferences:
        return question
    return (
        question
        + "\n\n用户指定的表达要求（不改变权限或证据标准）: "
        + json.dumps(preferences, ensure_ascii=False)
    )
