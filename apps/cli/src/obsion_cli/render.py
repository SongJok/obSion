from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from obsion_cli.runtime import AskResult

_EVENT_LABELS: Mapping[str, str] = {
    "context.resolved": "已解析上下文",
    "intent.detected": "正在理解问题",
    "plan.created": "正在规划",
    "plan.updated": "正在重规划",
    "capability.requested": "正在请求能力",
    "policy.checked": "正在评估策略",
    "policy.decided": "策略已裁决",
    "approval.requested": "等待审批",
    "tool.started": "正在调用能力",
    "tool.completed": "能力调用完成",
    "evidence.created": "已形成证据",
    "critic.started": "正在验证结论",
    "critic.completed": "验证完成",
    "answer.delta": "正在生成回答",
    "run.completed": "运行完成",
    "run.failed": "运行失败",
    "run.cancelled": "运行已取消",
}


def render_ask(result: AskResult, *, json_output: bool) -> str:
    if json_output:
        return json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    lines = [
        (
            f"Workspace {result.workspace.get('id')} · "
            f"Thread {result.thread.get('id')} · Run {result.run.get('id')}"
        ),
        f"status {result.run.get('status')}",
        "",
        "运行时间线",
    ]
    for event in result.events:
        name = str(event.get("name") or "")
        label = _EVENT_LABELS.get(name, name)
        detail = _event_detail(event)
        lines.append(f"  {label}" + (f" · {detail}" if detail else ""))
    if result.answer:
        lines.extend(["", "回答", result.answer.strip()])
    if result.claims:
        lines.extend(["", "Claims"])
        for claim in result.claims:
            statement = str(claim.get("statement") or "")
            confidence = claim.get("confidence")
            lines.append(f"  {statement}  (confidence {confidence})")
    if result.evidence:
        lines.extend(["", "Evidence"])
        for item in result.evidence:
            evidence_type = item.get("evidence_type") or item.get("type")
            source = item.get("source")
            resource = item.get("resource")
            lines.append(f"  {evidence_type} · {source} · {resource}")
    return "\n".join(lines).rstrip() + "\n"


def render_value(value: Any, *, json_output: bool) -> str:
    if json_output:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if isinstance(value, list):
        if not value:
            return "(empty)\n"
        return "\n".join(_summarize_item(item) for item in value) + "\n"
    if isinstance(value, dict):
        return _summarize_item(value) + "\n"
    return f"{value}\n"


def _summarize_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    identity = item.get("id") or item.get("name") or ""
    title = item.get("title") or item.get("statement") or item.get("status") or ""
    extra = item.get("status") if title != item.get("status") else item.get("kind")
    parts = [str(part) for part in (identity, title, extra) if part]
    return " · ".join(parts) if parts else json.dumps(item, ensure_ascii=False, sort_keys=True)


def _event_detail(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    for key in ("capability", "effect", "verified", "delta"):
        value = payload.get(key)
        if value is not None and value != "":
            text = str(value)
            return text if len(text) <= 80 else text[:77] + "..."
    return ""
