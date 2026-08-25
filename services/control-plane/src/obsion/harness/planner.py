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
    ) -> ExecutionPlan:
        route = understanding["route"]
        question = understanding["question"]
        time_range = understanding.get("time_range", {})
        if route == "KNOWLEDGE":
            return ExecutionPlan(
                route=route,
                steps=(
                    PlannedStep(
                        name="Search authorized enterprise knowledge",
                        capability="knowledge.search",
                        payload={"query": question, "limit": 8},
                        resource={"index": "organization"},
                        environment="development",
                    ),
                ),
                required_evidence=("DOCUMENT",),
                verification=("citation_coverage", "acl_retained", "question_coverage"),
            )
        if route == "DATA" and compiled_data_query is not None:
            return ExecutionPlan(
                route=route,
                steps=(
                    PlannedStep(
                        name="Execute governed read-only query",
                        capability="data.query",
                        payload={
                            "sql": compiled_data_query["sql"],
                            "parameters": compiled_data_query["parameters"],
                            "parameter_types": compiled_data_query["parameter_types"],
                        },
                        resource={
                            **compiled_data_query["lineage"],
                            "metric": compiled_data_query["metric"],
                            "dimensions": compiled_data_query["dimensions"],
                            "validation": compiled_data_query["validation"],
                        },
                        environment=compiled_data_query["environment"],
                    ),
                ),
                required_evidence=("DATA",),
                verification=("metric_definition", "sql_validated", "result_cited"),
            )
        if route == "ENGINEERING":
            return ExecutionPlan(
                route=route,
                steps=(
                    PlannedStep(
                        name="Search source code",
                        capability="code.search",
                        payload={"query": question},
                        resource={"scope": "authorized-repositories"},
                        environment="production",
                    ),
                ),
                required_evidence=("CODE",),
                verification=("symbol_lineage", "source_version", "question_coverage"),
            )
        incident_capabilities = (
            ("Query metric baseline", "metric.query", "METRIC"),
            ("Locate metric anomaly", "metric.anomaly", "METRIC"),
            ("Drill down dimensions", "metric.dimension", "METRIC"),
            ("Correlate deployments", "deployment.list", "DEPLOYMENT"),
            ("Aggregate error logs", "log.aggregate", "LOG"),
            ("Search representative traces", "trace.search", "TRACE"),
            ("Compare configuration", "config.diff", "CONFIG"),
            ("Inspect deployed code change", "git.diff", "CODE"),
        )
        steps = tuple(
            PlannedStep(
                name=name,
                capability=capability,
                payload={"query": question, "time_range": time_range},
                resource={"environment": "production", "evidence_type": evidence_type},
                environment="production",
                depends_on=() if index < 3 else (1,),
            )
            for index, (name, capability, evidence_type) in enumerate(
                incident_capabilities, start=1
            )
        )
        return ExecutionPlan(
            route="INCIDENT",
            steps=steps,
            required_evidence=("METRIC", "DEPLOYMENT", "LOG"),
            verification=("temporal_consistency", "conflict_detection", "alternative_causes"),
        )
