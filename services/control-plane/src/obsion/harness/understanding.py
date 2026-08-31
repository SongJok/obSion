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
    _ANALYTICS_TERMS = {
        "漏斗",
        "funnel",
        "转化",
        "趋势",
        "trend",
        "同比",
        "环比",
        "cohort",
        "业务分析",
    }
    _OPERATION_TERMS = {
        "k8s",
        "kubernetes",
        "工作负载",
        "副本",
        "rollout",
        "就绪探针",
        "pod",
    }
    _SUPPORT_TERMS = {
        "工单",
        "ticket",
        "客服",
        "投诉",
        "用户反馈",
        "customer support",
        "退款申请",
    }
    _L2_ROUTES = {
        "DATA",
        "ANALYTICS",
        "INCIDENT",
        "RESOURCE_ACCESS",
        "OPERATION",
        "SUPPORT",
    }

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
        has_analytics = any(term in normalized for term in self._ANALYTICS_TERMS)
        has_operation = any(term in normalized for term in self._OPERATION_TERMS)
        has_support = any(term in normalized for term in self._SUPPORT_TERMS)
        if compact in self._GREETING_FORMS:
            route = "CONVERSATION"
        elif has_resource_access:
            route = "RESOURCE_ACCESS"
        elif matched_metrics and has_analytics:
            route = "ANALYTICS"
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
        elif has_operation:
            route = "OPERATION"
        elif has_support:
            route = "SUPPORT"
        else:
            route = "KNOWLEDGE"
        return {
            **data_understanding,
            "domain": route,
            "route": route,
            "intent": (
                route
                if route in {"CONVERSATION", "RESOURCE_ACCESS", "SUPPORT", "OPERATION"}
                else data_understanding.get("intent", "ANALYTICS_QUERY")
            ),
            "question": question,
            "need_data": bool(matched_metrics) or has_resource_access,
            "need_root_cause": has_incident,
            "risk": "L2" if route in self._L2_ROUTES else "L1",
        }
