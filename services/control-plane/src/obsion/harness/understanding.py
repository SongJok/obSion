from typing import Any


class UnderstandingEngine:
    _GREETING_FORMS = {
        "hello",
        "hey",
        "hi",
        "你好",
        "您好",
        "早上好",
        "下午好",
        "晚上好",
    }
    _RESOURCE_ACCESS_TERMS = {
        "生产库",
        "生产数据库",
        "prod db",
        "production db",
        "production database",
        "production mysql",
        "production postgres",
    }
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
    _EXPLICIT_INCIDENT_TERMS = {
        "故障",
        "日志",
        "trace",
        "incident",
        "latency",
        "p99",
        "root cause",
    }
    _ENGINEERING_TERMS = {"代码", "commit", "diff", "调用链", "code", "repository", "git"}

    def route(self, question: str, data_understanding: dict[str, Any]) -> dict[str, Any]:
        normalized = question.casefold()
        compact = "".join(character for character in normalized.strip() if character.isalnum())
        matched_metrics = data_understanding.get("metrics", [])
        has_resource_access = any(term in normalized for term in self._RESOURCE_ACCESS_TERMS)
        has_incident = any(term in normalized for term in self._INCIDENT_TERMS)
        has_explicit_incident_signal = any(
            term in normalized for term in self._EXPLICIT_INCIDENT_TERMS
        )
        has_release_anomaly = "发布" in normalized and any(
            term in normalized
            for term in ("异常", "故障", "延迟", "latency", "p99", "根因", "root cause")
        )
        has_engineering = any(term in normalized for term in self._ENGINEERING_TERMS)
        if compact in self._GREETING_FORMS:
            route = "CONVERSATION"
        elif has_resource_access:
            route = "RESOURCE_ACCESS"
        elif matched_metrics:
            # Metric-bearing questions, including "why did it decline?", stay on
            # the governed DataAgent path. Root-cause analysis is then limited to
            # semantic dimensions; logs/traces require an explicit incident route.
            route = "DATA"
        elif has_incident and (
            has_engineering or has_explicit_incident_signal or has_release_anomaly
        ):
            route = "INCIDENT"
        elif has_engineering:
            route = "ENGINEERING"
        else:
            route = "KNOWLEDGE"
        return {
            **data_understanding,
            "domain": route,
            "route": route,
            "intent": (
                route
                if route in {"CONVERSATION", "RESOURCE_ACCESS"}
                else data_understanding.get("intent", "ANALYTICS_QUERY")
            ),
            "question": question,
            "need_data": bool(matched_metrics) or has_resource_access,
            "need_root_cause": has_incident,
            "risk": "L2" if route in {"DATA", "INCIDENT", "RESOURCE_ACCESS"} else "L1",
        }
