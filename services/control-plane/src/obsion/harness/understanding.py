import re
from typing import Any

from obsion.domain.task_context import requested_format

# Match a complete referential request, not a polite prefix on a new question.
_FOLLOWUP = re.compile(
    r"^(?:(?:请|麻烦|能否|可以|帮我|再|继续|把它|把上面的内容|对此|那)[，,\s]*)*"
    r"(?:详细(?:地)?(?:解释|说明|讲解|展开)?(?:一下|一点|一些)?|"
    r"(?:解释|说明|讲解)(?:得)?详细(?:一点|一些)?|"
    r"展开(?:说说|讲讲|一下)?|解释一下|补充(?:一下)?|继续|"
    r"(?:举|给)(?:个|一个|几个)?(?:例子|示例)|(?:再)?短一点|简短一点|"
    r"总结(?:一下)?|(?:用|换成|改成|以)(?:表格|列表|报告|段落|中文|英文)(?:形式)?(?:说明|解释|回答|总结|展示|呈现|输出)?|"
    r"为什么|有哪些例外|有什么限制|这个(?:结果|回答|结论)(?:是什么意思|怎么得出的))"
    r"[。？！!?,，\s]*$|"
    r"^(?:please\s+)?(?:explain (?:more|in detail)|tell me more|continue|"
    r"give (?:me )?an example|make it shorter|summarize (?:it|that))[.!?\s]*$",
    re.I,
)


def is_contextual_followup(question: str) -> bool:
    return bool(_FOLLOWUP.fullmatch(question.strip())) or requested_format(question) is not None


def task_question(question: str, previous_question: str | None) -> str:
    """Carry only a currently authorized task goal into a referential follow-up."""
    if not previous_question or not is_contextual_followup(question):
        return question
    goal = re.split(r"\n\n本轮追问[:：]", previous_question, maxsplit=1)[0]
    return f"{goal[:40000]}\n\n本轮追问: {question}"


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
    _ENGINEERING_TERMS = {
        "代码",
        "源码",
        "调用关系",
        "commit",
        "diff",
        "调用链",
        "code",
        "repository",
        "git",
    }
    _DOCUMENT_REQUEST = re.compile(
        r"(?:规范|制度|手册|指南|流程|准则|约定|标准|文档).{0,20}"
        r"(?:是什么|有哪些|要求|规定|解释|说明|介绍|怎么|如何|查询|查找|在哪)|"
        r"(?:查阅|查找|查询|解释|介绍|根据|依据|参照).{0,60}"
        r"(?:规范|制度|手册|指南|准则|标准)|"
        r"(?:代码评审|代码审查|编码|代码|发布|日志|故障处理)(?:规范|制度|流程|标准|指南)[？?。\s]*$|"
        r"\b(?:code review|coding|release|incident response) (?:policy|guidelines|standards)\b",
        re.I,
    )
    _SOURCE_INVESTIGATION = re.compile(
        r"(?:读取|搜索|修改|修复|定位|调试|分析|评审|审查).{0,20}"
        r"(?:源码|源代码|源文件|实现|提交|补丁|diff|commit)|"
        r"(?:调用链|调用关系|跨文件|堆栈|报错|根因)|"
        r"(?:[\w/-]+\.(?:py|go|php|tsx?|java|rs))\b",
        re.I,
    )
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
        elif self._DOCUMENT_REQUEST.search(question) and not self._SOURCE_INVESTIGATION.search(
            question
        ):
            route = "KNOWLEDGE"
        elif has_incident and (
            has_explicit_incident_signal
            or has_release_anomaly
            or (has_engineering and any(term in normalized for term in ("异常", "报错", "超时")))
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
