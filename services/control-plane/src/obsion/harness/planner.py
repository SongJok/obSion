from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PlannedStep:
    name: str
    capability: str
    payload: dict[str, Any]
    resource: dict[str, Any]
    environment: str
    depends_on: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    route: str
    steps: tuple[PlannedStep, ...]
    required_evidence: tuple[str, ...]
    verification: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "steps": [
                {
                    "ordinal": index,
                    "name": step.name,
                    "capability": step.capability,
                    "payload": step.payload,
                    "resource": step.resource,
                    "environment": step.environment,
                    "depends_on": step.depends_on,
                }
                for index, step in enumerate(self.steps, start=1)
            ],
            "required_evidence": self.required_evidence,
            "verification": self.verification,
        }


class Planner:
    def create(
        self,
        understanding: dict[str, Any],
        *,
        compiled_data_query: dict[str, Any] | None = None,
        available_capabilities: frozenset[str] | None = None,
    ) -> ExecutionPlan:
        route = understanding["route"]
        question = understanding["question"]
        time_range = understanding.get("time_range", {})
        available = available_capabilities

        def can_select(capability: str) -> bool:
            return available is None or capability in available

        if route == "CONVERSATION":
            return ExecutionPlan(
                route=route,
                steps=(),
                required_evidence=(),
                verification=("non_factual_response",),
            )
        if route == "RESOURCE_ACCESS":
            resource_steps: tuple[PlannedStep, ...] = (
                (
                    PlannedStep(
                        name="Request governed production data access",
                        capability="data.query",
                        payload={"request": question, "purpose": "direct_resource_access"},
                        resource={
                            "environment": "production",
                            "resource_type": "database",
                            "access": "read",
                        },
                        environment="production",
                    ),
                )
                if can_select("data.query")
                else ()
            )
            return ExecutionPlan(
                route=route,
                steps=resource_steps,
                required_evidence=("DATA",),
                verification=("capability_required", "no_direct_resource_access"),
            )
        if route == "KNOWLEDGE":
            knowledge_steps: tuple[PlannedStep, ...] = (
                (
                    PlannedStep(
                        name="Search authorized enterprise knowledge",
                        capability="knowledge.search",
                        payload={"query": question, "limit": 8},
                        resource={"index": "organization"},
                        environment="development",
                    ),
                )
                if can_select("knowledge.search")
                else ()
            )
            return ExecutionPlan(
                route=route,
                steps=knowledge_steps,
                required_evidence=("DOCUMENT",),
                verification=("citation_coverage", "acl_retained", "question_coverage"),
            )
        if route in {"DATA", "ANALYTICS"} and compiled_data_query is not None:
            data_steps: tuple[PlannedStep, ...] = (
                (
                    PlannedStep(
                        name="Execute governed read-only query",
                        capability="data.query",
                        payload={
                            "sql": compiled_data_query["sql"],
                            "parameters": compiled_data_query["parameters"],
                            "parameter_types": compiled_data_query["parameter_types"],
                            "column_masks": compiled_data_query.get("column_masks", {}),
                        },
                        resource={
                            **compiled_data_query["lineage"],
                            "metric": compiled_data_query["metric"],
                            "dimensions": compiled_data_query["dimensions"],
                            "validation": compiled_data_query["validation"],
                        },
                        environment=compiled_data_query["environment"],
                    ),
                )
                if can_select("data.query")
                else ()
            )
            return ExecutionPlan(
                route=route,
                steps=data_steps,
                required_evidence=("DATA",),
                verification=("metric_definition", "sql_validated", "result_cited"),
            )
        if route == "ENGINEERING":
            engineering_steps: list[PlannedStep] = []
            if can_select("code.symbol"):
                engineering_steps.append(
                    PlannedStep(
                        name="Resolve authorized source symbols",
                        capability="code.symbol",
                        payload={
                            "operation": "code.symbol",
                            "query": question,
                            "repository": str(understanding.get("repository") or "*"),
                        },
                        resource={"index": "organization", "scope": "authorized-code-graph"},
                        environment="development",
                    )
                )
            if can_select("code.reference") and engineering_steps:
                engineering_steps.append(
                    PlannedStep(
                        name="Find symbol references",
                        capability="code.reference",
                        payload={
                            "operation": "code.reference",
                            "symbol": question,
                            "repository": str(understanding.get("repository") or "*"),
                        },
                        resource={"index": "organization", "scope": "authorized-code-graph"},
                        environment="development",
                        depends_on=(1,),
                    )
                )
            if can_select("code.callers") and engineering_steps:
                engineering_steps.append(
                    PlannedStep(
                        name="Traverse callers",
                        capability="code.callers",
                        payload={
                            "operation": "code.callers",
                            "symbol": question,
                            "repository": str(understanding.get("repository") or "*"),
                        },
                        resource={"index": "organization", "scope": "authorized-code-graph"},
                        environment="development",
                        depends_on=(len(engineering_steps),),
                    )
                )
            if not engineering_steps and can_select("code.search"):
                engineering_steps.append(
                    PlannedStep(
                        name="Search source code",
                        capability="code.search",
                        payload={
                            "operation": "code.search",
                            "repository": str(understanding.get("repository") or "*"),
                            "query": question,
                        },
                        resource={"scope": "authorized-repositories"},
                        environment="production",
                    )
                )
            return ExecutionPlan(
                route=route,
                steps=tuple(engineering_steps),
                required_evidence=("CODE",),
                verification=("symbol_lineage", "source_version", "question_coverage"),
            )
        if route == "SUPPORT":
            support_steps: list[PlannedStep] = []
            if can_select("ticket.search"):
                support_steps.append(
                    PlannedStep(
                        name="Search authorized support tickets",
                        capability="ticket.search",
                        payload={
                            "operation": "ticket.search",
                            "query": question,
                            "limit": 8,
                        },
                        resource={"index": "organization", "source": "ticket"},
                        environment="development",
                    )
                )
            if can_select("knowledge.search"):
                support_steps.append(
                    PlannedStep(
                        name="Search authorized support knowledge",
                        capability="knowledge.search",
                        payload={"query": question, "limit": 8},
                        resource={"index": "organization"},
                        environment="development",
                        depends_on=(len(support_steps),) if support_steps else (),
                    )
                )
            return ExecutionPlan(
                route=route,
                steps=tuple(support_steps),
                required_evidence=("DOCUMENT",),
                verification=("citation_coverage", "acl_retained", "no_write_path"),
            )
        if route == "OPERATION":
            operation_capabilities = (
                ("Inspect Kubernetes workload status", "k8s.status", "TOOL"),
                ("List authorized deployments", "deployment.list", "DEPLOYMENT"),
                ("Read effective configuration", "config.get", "CONFIG"),
                ("Compare configuration", "config.diff", "CONFIG"),
                ("Search authorized operational logs", "log.search", "LOG"),
                ("Query metric baseline", "metric.query", "METRIC"),
            )
            operation_steps: list[PlannedStep] = []
            for name, capability, evidence_type in operation_capabilities:
                if not can_select(capability):
                    continue
                operation_payload: dict[str, Any] = {
                    "operation": capability,
                    "query": question,
                    "service": str(understanding.get("service") or "*"),
                    "time_range": time_range,
                    "environment": "production",
                }
                if isinstance(time_range, dict):
                    if isinstance(time_range.get("start"), str):
                        operation_payload["start_time"] = time_range["start"]
                    if isinstance(time_range.get("end"), str):
                        operation_payload["end_time"] = time_range["end"]
                operation_steps.append(
                    PlannedStep(
                        name=name,
                        capability=capability,
                        payload=operation_payload,
                        resource={"environment": "production", "evidence_type": evidence_type},
                        environment="production",
                        depends_on=tuple(range(1, len(operation_steps) + 1)[-1:]),
                    )
                )
            return ExecutionPlan(
                route=route,
                steps=tuple(operation_steps),
                required_evidence=tuple(
                    dict.fromkeys(step.resource["evidence_type"] for step in operation_steps)
                ),
                verification=("read_only_status", "time_window_bounded", "no_write_path"),
            )
        if route != "INCIDENT":
            return ExecutionPlan(
                route=route,
                steps=(),
                required_evidence=(),
                verification=("unsupported_route",),
            )
        incident_capabilities = (
            ("Query metric baseline", "metric.query", "METRIC", ()),
            ("Compare metric periods", "metric.compare", "METRIC", (1,)),
            ("Locate metric anomaly", "metric.anomaly", "METRIC", (2,)),
            ("Drill down dimensions", "metric.dimension", "METRIC", (3,)),
            ("Correlate deployments", "deployment.list", "DEPLOYMENT", (4,)),
            ("Aggregate error logs", "log.aggregate", "LOG", (5,)),
            ("Search production logs", "log.search", "LOG", (6,)),
            ("Search representative traces", "trace.search", "TRACE", (7,)),
            ("Compare configuration", "config.diff", "CONFIG", (7,)),
            ("Inspect Kubernetes workload status", "k8s.status", "TOOL", (7,)),
            ("Inspect deployed code change", "git.diff", "GIT", (7,)),
        )
        selected_steps: list[PlannedStep] = []
        ordinal_map: dict[int, int] = {}
        for source_index, item in enumerate(incident_capabilities, start=1):
            name, capability, evidence_type, depends_on = item
            if not can_select(capability):
                continue
            ordinal_map[source_index] = len(selected_steps) + 1
            payload: dict[str, Any] = {
                "operation": capability,
                "query": question,
                "service": str(understanding.get("service") or "*"),
                "time_range": time_range,
                "environment": "production",
            }
            if isinstance(time_range, dict):
                if isinstance(time_range.get("start"), str):
                    payload["start_time"] = time_range["start"]
                if isinstance(time_range.get("end"), str):
                    payload["end_time"] = time_range["end"]
            metrics = understanding.get("metrics")
            if isinstance(metrics, list) and metrics and isinstance(metrics[0], dict):
                metric_name = metrics[0].get("name") or metrics[0].get("id")
                if isinstance(metric_name, str) and metric_name:
                    payload["metric"] = metric_name
            if capability in {"git.diff", "git.history", "git.commit", "code.search"}:
                payload["repository"] = str(understanding.get("repository") or "*")
                selected_resource_repository = payload["repository"]
            else:
                selected_resource_repository = None
            dependency_ordinals: list[int] = []
            for dependency in depends_on:
                if dependency in ordinal_map:
                    dependency_ordinals.append(ordinal_map[dependency])
                    continue
                prior = [ordinal for source, ordinal in ordinal_map.items() if source < dependency]
                if prior:
                    dependency_ordinals.append(max(prior))
            selected_steps.append(
                PlannedStep(
                    name=name,
                    capability=capability,
                    payload=payload,
                    resource={
                        "environment": "production",
                        "evidence_type": evidence_type,
                        **(
                            {"repository": selected_resource_repository}
                            if selected_resource_repository is not None
                            else {}
                        ),
                    },
                    environment="production",
                    depends_on=tuple(dict.fromkeys(dependency_ordinals)),
                )
            )
        incident_steps: tuple[PlannedStep, ...] = tuple(selected_steps)
        return ExecutionPlan(
            route="INCIDENT",
            steps=incident_steps,
            required_evidence=("METRIC", "DEPLOYMENT", "LOG"),
            verification=("temporal_consistency", "conflict_detection", "alternative_causes"),
        )
