"""Controlled synthesis outcomes; model prose never becomes an abstention reply."""

import html
import re
from enum import StrEnum
from typing import Any

from obsion.db.models import Run


class GenerationStatus(StrEnum):
    UNASSESSED = "UNASSESSED"
    CANDIDATE = "CANDIDATE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


def record_generation(
    run: Run,
    status: GenerationStatus,
    *,
    question: str = "",
    excerpt: Any = None,
) -> None:
    if run.plan.get("route") not in {"KNOWLEDGE", "SUPPORT"}:
        return
    metadata: dict[str, str] = {"status": status.value}
    # The model may select a bounded, exact portion of the sanitized USER
    # question, but cannot add facts, source excerpts, instructions or advice.
    if (
        status == GenerationStatus.INSUFFICIENT_EVIDENCE
        and isinstance(excerpt, str)
        and 2 <= len(excerpt.strip()) <= 160
        and excerpt.strip() in question
    ):
        metadata["requested_information"] = excerpt.strip()
    run.plan = {**run.plan, "answer_generation": metadata}


def generation_reply(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    status = metadata.get("status")
    if status == GenerationStatus.INSUFFICIENT_EVIDENCE:
        requested = metadata.get("requested_information")
        topic = "这个问题"
        if isinstance(requested, str) and requested.strip():
            # Keep the selected user words as text, including Markdown/HTML.
            plain = html.escape(" ".join(requested.split()), quote=False)
            plain = re.sub(r"([\\`*_{}\[\]()#+.!|>~])", r"\\\1", plain)
            topic = f"「{plain}」"
        return (
            f"不知道：本次取得的授权资料不足以确认{topic}。"
            "请补充直接说明这一事项的资料，或指定其他已授权来源。"
        )
    if status == GenerationStatus.MODEL_UNAVAILABLE:
        return "不知道：当前回答服务暂时不可用，本次未能生成答案。请稍后重试。"
    if status == GenerationStatus.INVALID_OUTPUT:
        return "不知道：本次未能生成有效、可核验的回答，因此暂不发布结论。请重试。"
    return None
