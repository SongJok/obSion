from typing import Any


class UnderstandingEngine:
    _INCIDENT_TERMS = {
        "故障",
        "异常",
        "为什么",
        "日志",
        "发布",
        "trace",
        "incident",
        "latency",
        "p99",
        "root cause",
    }
    _ENGINEERING_TERMS = {"代码", "commit", "diff", "调用链", "code", "repository", "git"}

    def route(self, question: str, data_understanding: dict[str, Any]) -> dict[str, Any]:
        normalized = question.casefold()
        matched_metrics = data_understanding.get("metrics", [])
        has_incident = any(term in normalized for term in self._INCIDENT_TERMS)
        has_engineering = any(term in normalized for term in self._ENGINEERING_TERMS)
        if matched_metrics and has_incident:
            route = "INCIDENT"
        elif matched_metrics:
            route = "DATA"
        elif has_incident and has_engineering:
            route = "INCIDENT"
        elif has_engineering:
            route = "ENGINEERING"
        else:
            route = "KNOWLEDGE"
        return {
            **data_understanding,
            "domain": route,
            "route": route,
            "question": question,
            "need_data": bool(matched_metrics),
            "need_root_cause": has_incident,
            "risk": "L2" if route in {"DATA", "INCIDENT"} else "L1",
        }
