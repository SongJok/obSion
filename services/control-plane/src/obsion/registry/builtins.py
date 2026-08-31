import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.plugin_governance import development_plugin_configuration
from obsion.capabilities.vendor_knowledge import (
    CONTAINERS_INPUT_SCHEMA,
    CONTAINERS_OUTPUT_SCHEMA,
    ITEMS_INPUT_SCHEMA,
    ITEMS_OUTPUT_SCHEMA,
    KNOWLEDGE_SOURCE_CONTAINERS,
    KNOWLEDGE_SOURCE_ITEMS,
    VENDOR_KNOWLEDGE_BROWSE_OPERATIONS,
)
from obsion.common.time import utc_now
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    ModelProfile,
    PromptDefinition,
    PromptVersion,
    SkillDefinition,
    SkillVersion,
)
from obsion.domain.enums import (
    CapabilityTransport,
    Classification,
    ConnectorStatus,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.registry.agent_spec import ALLOWED_SANDBOX_MOUNTS, AgentSpec
from obsion.registry.capability_descriptor import CapabilityDescriptor
from obsion.registry.manifests import load_registry_specs
from obsion.registry.prompt_pins import (
    DEFAULT_SYSTEM_POLICY_SCHEMA,
    DEFAULT_SYSTEM_POLICY_TEMPLATE,
    SYSTEM_POLICY_PROMPT_NAME,
    prompt_checksum,
)


def _checksum(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


_DEFAULT_SANDBOX: dict[str, Any] = {
    "enabled": True,
    "network": "gateway-only",
    "mounts": list(ALLOWED_SANDBOX_MOUNTS),
}


@dataclass(frozen=True, slots=True)
class CapabilitySeed:
    name: str
    description: str
    permission: str
    evidence_type: str
    risk: RiskLevel = RiskLevel.L1
    transport: CapabilityTransport = CapabilityTransport.HTTP
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    side_effect: SideEffect = SideEffect.NONE


def _action_input_schema(
    action_type: str,
    purpose: str,
    *,
    target_properties: dict[str, Any],
    target_required: list[str],
    parameter_properties: dict[str, Any],
    parameter_required: list[str],
    rollback: bool = False,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "action_type": {"const": action_type},
        "purpose": {"const": purpose},
        "target": {
            "type": "object",
            "additionalProperties": False,
            "required": target_required,
            "properties": target_properties,
        },
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": parameter_required,
            "properties": parameter_properties,
        },
        "obsion": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action_request_id", "plan_checksum_sha256"],
            "properties": {
                "action_request_id": {"type": "string", "format": "uuid"},
                "plan_checksum_sha256": {
                    "type": "string",
                    "pattern": "^[a-f0-9]{64}$",
                },
            },
        },
    }
    required = ["action_type", "purpose", "target", "parameters", "obsion"]
    if rollback:
        properties["original_output"] = {"type": "object"}
        required.append("original_output")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _action_output_schema(*, state: str | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "external_id": {"type": "string", "minLength": 1, "maxLength": 500},
    }
    required = ["external_id"]
    if state is None:
        properties["url"] = {
            "type": "string",
            "format": "uri",
            "minLength": 1,
            "maxLength": 4000,
        }
        required.append("url")
    else:
        properties["state"] = {"const": state}
        required.append("state")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


_REPOSITORY_PROPERTY = {
    "repository": {
        "type": "string",
        "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
    }
}
_PROJECT_PROPERTY = {
    "project_key": {
        "type": "string",
        "pattern": "^[A-Za-z][A-Za-z0-9_-]{1,39}$",
    }
}


def _observability_input_schema(operation: str, *, query_required: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "operation": {"const": operation},
        "service": {"type": "string", "minLength": 1, "maxLength": 240},
        "environment": {"type": "string", "minLength": 1, "maxLength": 80},
        "metric": {"type": "string", "minLength": 1, "maxLength": 500},
        "query": {"type": "string", "minLength": 1, "maxLength": 4000},
        "start_time": {"type": "string", "format": "date-time"},
        "end_time": {"type": "string", "format": "date-time"},
        "compare_start_time": {"type": "string", "format": "date-time"},
        "compare_end_time": {"type": "string", "format": "date-time"},
        "time_range": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "timezone": {"type": "string", "minLength": 1, "maxLength": 80},
            },
        },
        "filters": {"type": "object"},
        "group_by": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "minLength": 1, "maxLength": 120},
        },
        "step_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
        "threshold": {"type": "number", "minimum": 0},
        "trace_id": {"type": "string", "minLength": 1, "maxLength": 128},
    }
    required = ["operation", "service", "start_time", "end_time"]
    if query_required:
        required.append("query")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _observability_output_schema(operation: str) -> dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    event = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "timestamp",
            "service",
            "environment",
            "trace_id",
            "request_id",
            "user_id_hash",
            "order_id_hash",
            "deployment_id",
            "commit_id",
            "host",
            "pod",
            "severity",
            "attributes",
        ],
        "properties": {
            "timestamp": {"type": "string", "minLength": 1},
            "service": {"type": "string", "minLength": 1},
            "environment": {"type": "string", "minLength": 1},
            "trace_id": nullable_string,
            "request_id": nullable_string,
            "user_id_hash": nullable_string,
            "order_id_hash": nullable_string,
            "deployment_id": nullable_string,
            "commit_id": nullable_string,
            "host": nullable_string,
            "pod": nullable_string,
            "severity": nullable_string,
            "attributes": {"type": "object"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "events", "count", "next_cursor"],
        "properties": {
            "operation": {"const": operation},
            "events": {"type": "array", "items": event},
            "count": {"type": "integer", "minimum": 0},
            "next_cursor": {"type": ["string", "null"]},
        },
    }


def _engineering_input_schema(operation: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "operation": {"const": operation},
        "repository": {
            "type": "string",
            "minLength": 1,
            "maxLength": 240,
            "pattern": r"^(?:\*|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$",
        },
        "service": {"type": "string", "minLength": 1, "maxLength": 240},
        "environment": {"type": "string", "minLength": 1, "maxLength": 80},
        "query": {"type": "string", "minLength": 1, "maxLength": 4000},
        "commit_id": {"type": "string", "minLength": 7, "maxLength": 200},
        "deployment_id": {"type": "string", "minLength": 1, "maxLength": 240},
        "start_time": {"type": "string", "format": "date-time"},
        "end_time": {"type": "string", "format": "date-time"},
        "time_range": {"type": "object"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
        "workload": {"type": "string", "minLength": 1, "maxLength": 240},
        "namespace": {"type": "string", "minLength": 1, "maxLength": 240},
        "key": {"type": "string", "minLength": 1, "maxLength": 500},
    }
    required = ["operation", "repository"]
    if operation == "code.search":
        required.append("query")
    elif operation == "git.commit":
        required.append("commit_id")
    elif operation == "git.blame":
        required.append("path")
        properties["path"] = {"type": "string", "minLength": 1, "maxLength": 1024}
    elif operation == "deployment.commit":
        required.append("deployment_id")
    elif operation in {"git.diff", "git.history"}:
        required.extend(["start_time", "end_time"])
    elif operation in {"config.get", "config.diff", "k8s.status"}:
        required = ["operation", "service"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _engineering_output_schema(operation: str) -> dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "timestamp",
            "repository",
            "commit_id",
            "deployment_id",
            "service",
            "environment",
            "author_hash",
            "title",
            "status",
            "attributes",
        ],
        "properties": {
            "timestamp": {"type": "string", "minLength": 1},
            "repository": {"type": "string", "minLength": 1},
            "commit_id": nullable_string,
            "deployment_id": nullable_string,
            "service": nullable_string,
            "environment": nullable_string,
            "author_hash": nullable_string,
            "title": nullable_string,
            "status": nullable_string,
            "attributes": {"type": "object"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "items", "count", "next_cursor"],
        "properties": {
            "operation": {"const": operation},
            "items": {"type": "array", "items": item},
            "count": {"type": "integer", "minimum": 0},
            "next_cursor": {"type": ["string", "null"]},
        },
    }


def _code_graph_input_schema(operation: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "operation": {"const": operation},
        "query": {"type": "string", "minLength": 1, "maxLength": 4000},
        "symbol": {"type": "string", "minLength": 1, "maxLength": 1000},
        "qualified_name": {"type": "string", "minLength": 1, "maxLength": 1000},
        "repository": {"type": "string", "minLength": 1, "maxLength": 240},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }
    required = ["operation"]
    if operation == "code.symbol":
        required.append("query")
    else:
        required.append("symbol")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _code_graph_output_schema(operation: str) -> dict[str, Any]:
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "repository_id",
            "repository",
            "commit_id",
            "snapshot_id",
            "symbol_id",
            "path",
            "language",
            "kind",
            "name",
            "qualified_name",
            "start_line",
            "end_line",
            "relations",
        ],
        "properties": {
            "repository_id": {"type": "string"},
            "repository": {"type": "string"},
            "commit_id": {"type": "string"},
            "snapshot_id": {"type": "string"},
            "symbol_id": {"type": "string"},
            "path": {"type": "string"},
            "language": {"type": "string"},
            "kind": {"type": "string"},
            "name": {"type": "string"},
            "qualified_name": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "relations": {"type": "array", "items": {"type": "object"}},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "items", "count"],
        "properties": {
            "operation": {"const": operation},
            "items": {"type": "array", "items": item},
            "count": {"type": "integer", "minimum": 0},
        },
    }


_CAPABILITIES = [
    CapabilitySeed(
        "code.search",
        "Search indexed source code",
        "code.read",
        "CODE",
        transport=CapabilityTransport.HTTP,
        input_schema=_engineering_input_schema("code.search"),
        output_schema=_engineering_output_schema("code.search"),
    ),
    CapabilitySeed(
        "code.symbol",
        "Resolve a source symbol",
        "code.read",
        "CODE",
        transport=CapabilityTransport.INTERNAL,
        input_schema=_code_graph_input_schema("code.symbol"),
        output_schema=_code_graph_output_schema("code.symbol"),
    ),
    CapabilitySeed(
        "code.reference",
        "Find symbol references",
        "code.read",
        "CODE",
        transport=CapabilityTransport.INTERNAL,
        input_schema=_code_graph_input_schema("code.reference"),
        output_schema=_code_graph_output_schema("code.reference"),
    ),
    CapabilitySeed(
        "code.callers",
        "Find callers of a symbol",
        "code.read",
        "CODE",
        transport=CapabilityTransport.INTERNAL,
        input_schema=_code_graph_input_schema("code.callers"),
        output_schema=_code_graph_output_schema("code.callers"),
    ),
    CapabilitySeed(
        "code.callees",
        "Find callees of a symbol",
        "code.read",
        "CODE",
        transport=CapabilityTransport.INTERNAL,
        input_schema=_code_graph_input_schema("code.callees"),
        output_schema=_code_graph_output_schema("code.callees"),
    ),
    CapabilitySeed(
        "git.commit",
        "Read commit metadata",
        "code.read",
        "GIT",
        transport=CapabilityTransport.HTTP,
        input_schema=_engineering_input_schema("git.commit"),
        output_schema=_engineering_output_schema("git.commit"),
    ),
    CapabilitySeed(
        "git.diff",
        "Read an immutable Git diff",
        "code.read",
        "GIT",
        transport=CapabilityTransport.HTTP,
        input_schema=_engineering_input_schema("git.diff"),
        output_schema=_engineering_output_schema("git.diff"),
    ),
    CapabilitySeed(
        "git.blame",
        "Read Git blame attribution",
        "code.read",
        "GIT",
        transport=CapabilityTransport.HTTP,
        input_schema=_engineering_input_schema("git.blame"),
        output_schema=_engineering_output_schema("git.blame"),
    ),
    CapabilitySeed(
        "git.history",
        "Read repository history",
        "code.read",
        "GIT",
        transport=CapabilityTransport.HTTP,
        input_schema=_engineering_input_schema("git.history"),
        output_schema=_engineering_output_schema("git.history"),
    ),
    CapabilitySeed(
        "deployment.commit",
        "Resolve deployed commit lineage",
        "deployment.read",
        "DEPLOYMENT",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _engineering_input_schema("deployment.commit"),
        _engineering_output_schema("deployment.commit"),
    ),
    CapabilitySeed(
        "log.search",
        "Search authorized logs",
        "logs.read",
        "LOG",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _observability_input_schema("log.search", query_required=True),
        _observability_output_schema("log.search"),
    ),
    CapabilitySeed("log.error", "Search error events", "logs.read", "LOG", RiskLevel.L2),
    CapabilitySeed(
        "log.aggregate",
        "Aggregate log events",
        "logs.read",
        "LOG",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _observability_input_schema("log.aggregate", query_required=True),
        _observability_output_schema("log.aggregate"),
    ),
    CapabilitySeed(
        "trace.search",
        "Search distributed traces",
        "traces.read",
        "TRACE",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _observability_input_schema("trace.search"),
        _observability_output_schema("trace.search"),
    ),
    CapabilitySeed(
        "trace.timeline",
        "Build a trace timeline",
        "traces.read",
        "TRACE",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _observability_input_schema("trace.timeline"),
        _observability_output_schema("trace.timeline"),
    ),
    CapabilitySeed(
        "metric.query",
        "Query a governed metric",
        "metrics.read",
        "METRIC",
        transport=CapabilityTransport.HTTP,
        input_schema=_observability_input_schema("metric.query"),
        output_schema=_observability_output_schema("metric.query"),
    ),
    CapabilitySeed(
        "metric.compare",
        "Compare metric periods",
        "metrics.read",
        "METRIC",
        transport=CapabilityTransport.HTTP,
        input_schema=_observability_input_schema("metric.compare"),
        output_schema=_observability_output_schema("metric.compare"),
    ),
    CapabilitySeed(
        "metric.anomaly",
        "Detect metric anomalies",
        "metrics.read",
        "METRIC",
        transport=CapabilityTransport.HTTP,
        input_schema=_observability_input_schema("metric.anomaly"),
        output_schema=_observability_output_schema("metric.anomaly"),
    ),
    CapabilitySeed(
        "metric.dimension",
        "Drill down metric dimensions",
        "metrics.read",
        "METRIC",
        transport=CapabilityTransport.HTTP,
        input_schema=_observability_input_schema("metric.dimension"),
        output_schema=_observability_output_schema("metric.dimension"),
    ),
    CapabilitySeed("schema.search", "Search authorized schemas", "data.metadata.read", "DATA"),
    CapabilitySeed("table.describe", "Describe an authorized table", "data.metadata.read", "DATA"),
    CapabilitySeed(
        "metric.describe", "Read a governed metric definition", "data.metadata.read", "DATA"
    ),
    CapabilitySeed(
        "data.query",
        "Execute validated read-only SQL",
        "data.query.read",
        "DATA",
        RiskLevel.L2,
        CapabilityTransport.SQL_PROXY,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["sql", "parameters", "parameter_types"],
            "properties": {
                "sql": {"type": "string", "minLength": 1, "maxLength": 100000},
                "parameters": {"type": "array", "maxItems": 1000},
                "parameter_types": {
                    "type": "array",
                    "maxItems": 1000,
                    "items": {"type": "string", "enum": ["scalar", "datetime"]},
                },
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                "scan_budget": {"type": "integer", "minimum": 1, "maximum": 1000000000},
                "row_policy": {"type": "object"},
                "column_masks": {"type": "object"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["columns", "rows", "row_count"],
            "properties": {
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {"type": "array", "items": {"type": "object"}},
                "row_count": {"type": "integer", "minimum": 0},
            },
        },
    ),
    CapabilitySeed(
        "data.preview",
        "Preview a bounded authorized dataset",
        "data.query.read",
        "DATA",
        RiskLevel.L2,
        CapabilityTransport.SQL_PROXY,
    ),
    CapabilitySeed(
        "sql.validate",
        "Validate SQL policy",
        "data.metadata.read",
        "DATA",
        RiskLevel.L0,
        CapabilityTransport.INTERNAL,
    ),
    CapabilitySeed(
        "sql.explain",
        "Explain a read-only query plan",
        "data.query.read",
        "DATA",
        RiskLevel.L2,
        CapabilityTransport.SQL_PROXY,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["sql", "parameters", "parameter_types"],
            "properties": {
                "sql": {"type": "string", "minLength": 1, "maxLength": 100000},
                "parameters": {"type": "array", "maxItems": 1000},
                "parameter_types": {
                    "type": "array",
                    "maxItems": 1000,
                    "items": {"type": "string", "enum": ["scalar", "datetime"]},
                },
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
                "explain": {"type": "boolean"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["plan", "validation"],
            "properties": {
                "plan": {"type": "object"},
                "validation": {"type": "object"},
            },
        },
    ),
    CapabilitySeed(
        "knowledge.search",
        "Search ACL-filtered enterprise knowledge",
        "knowledge.read",
        "DOCUMENT",
        RiskLevel.L1,
        CapabilityTransport.INTERNAL,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
        {
            "type": "object",
            "required": ["query", "hits", "count"],
            "properties": {
                "query": {"type": "string"},
                "hits": {"type": "array"},
                "count": {"type": "integer"},
            },
        },
    ),
    CapabilitySeed(
        KNOWLEDGE_SOURCE_CONTAINERS,
        "Browse authorized vendor Knowledge spaces and workspaces",
        "knowledge.write",
        "DOCUMENT",
        RiskLevel.L1,
        CapabilityTransport.HTTP,
        CONTAINERS_INPUT_SCHEMA,
        CONTAINERS_OUTPUT_SCHEMA,
    ),
    CapabilitySeed(
        KNOWLEDGE_SOURCE_ITEMS,
        "Browse authorized vendor Knowledge nodes and pages",
        "knowledge.write",
        "DOCUMENT",
        RiskLevel.L1,
        CapabilityTransport.HTTP,
        ITEMS_INPUT_SCHEMA,
        ITEMS_OUTPUT_SCHEMA,
    ),
    CapabilitySeed(
        "knowledge.ingest",
        "Ingest an authorized vendor document into the Knowledge pipeline",
        "knowledge.write",
        "DOCUMENT",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "document_id"],
            "properties": {
                "operation": {"type": "string", "const": "knowledge.ingest"},
                "document_id": {"type": "string", "minLength": 2, "maxLength": 128},
                "page_id": {"type": "string", "minLength": 1, "maxLength": 20},
                "obj_type": {"type": "string", "enum": ["auto", "docx", "wiki"]},
                "title": {"type": "string", "maxLength": 500},
                "classification": {
                    "type": "string",
                    "enum": ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"],
                },
                "acl": {"type": "object"},
                "inherit_acl": {"type": "boolean"},
            },
        },
        {
            "type": "object",
            "required": [
                "document_id",
                "version_id",
                "source",
                "external_id",
                "title",
                "version",
                "chunk_count",
                "operation",
            ],
            "properties": {
                "document_id": {"type": "string"},
                "version_id": {"type": "string"},
                "source": {
                    "type": "string",
                    "enum": ["feishu", "dingtalk", "wecom", "confluence"],
                },
                "external_id": {"type": "string"},
                "title": {"type": "string"},
                "version": {"type": "integer"},
                "chunk_count": {"type": "integer"},
                "revision_id": {"type": ["string", "null"]},
                "obj_type": {"type": "string"},
                "workspace_id": {"type": ["string", "null"]},
                "space_id": {"type": ["string", "null"]},
                "operation": {"type": "string", "const": "knowledge.ingest"},
            },
        },
        side_effect=SideEffect.IDEMPOTENT_WRITE,
    ),
    CapabilitySeed(
        "knowledge.sync",
        "Sync an authorized vendor knowledge space into the Knowledge pipeline",
        "knowledge.write",
        "DOCUMENT",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "space_id"],
            "properties": {
                "operation": {"type": "string", "const": "knowledge.sync"},
                "space_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "classification": {
                    "type": "string",
                    "enum": ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"],
                },
                "acl": {"type": "object"},
                "inherit_acl": {"type": "boolean"},
            },
        },
        {
            "type": "object",
            "required": [
                "operation",
                "ingested",
                "skipped",
                "failed",
                "ingested_count",
                "skipped_count",
                "failed_count",
            ],
            "properties": {
                "operation": {"type": "string", "const": "knowledge.sync"},
                "space_id": {"type": "string"},
                "workspace_id": {"type": "string"},
                "ingested": {"type": "array"},
                "skipped": {"type": "array"},
                "failed": {"type": "array"},
                "ingested_count": {"type": "integer"},
                "skipped_count": {"type": "integer"},
                "failed_count": {"type": "integer"},
            },
            "anyOf": [{"required": ["space_id"]}, {"required": ["workspace_id"]}],
        },
        side_effect=SideEffect.IDEMPOTENT_WRITE,
    ),
    CapabilitySeed(
        "document.read", "Read an authorized document version", "knowledge.read", "DOCUMENT"
    ),
    CapabilitySeed(
        "policy.search", "Search policies available to a principal", "policy.read", "DOCUMENT"
    ),
    CapabilitySeed(
        "ticket.search",
        "Search ACL-filtered support tickets",
        "knowledge.read",
        "DOCUMENT",
        RiskLevel.L1,
        CapabilityTransport.INTERNAL,
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "query"],
            "properties": {
                "operation": {"type": "string", "const": "ticket.search"},
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
        {
            "type": "object",
            "required": ["query", "hits", "count"],
            "properties": {
                "query": {"type": "string"},
                "hits": {"type": "array"},
                "count": {"type": "integer"},
            },
        },
    ),
    CapabilitySeed(
        "mcp.development.echo",
        "In-process MCP development echo. Remote MCP servers are not implemented.",
        "mcp.invoke",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.MCP,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "arguments": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["protocol", "adapter", "tool", "echo", "run_id"],
            "properties": {
                "protocol": {"const": "mcp"},
                "protocol_version": {"type": "string"},
                "adapter": {"const": "in-process"},
                "tool": {"type": "string"},
                "echo": {"type": "object"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "sdk.development.echo",
        "In-process SDK development echo. Remote SDK installs are not implemented.",
        "sdk.invoke",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.SDK,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sdk": {"type": "string", "minLength": 1, "maxLength": 200},
                "method": {"type": "string", "minLength": 1, "maxLength": 200},
                "arguments": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["protocol", "adapter", "sdk", "method", "echo", "run_id"],
            "properties": {
                "protocol": {"const": "sdk"},
                "adapter": {"const": "in-process"},
                "sdk": {"type": "string"},
                "method": {"type": "string"},
                "echo": {"type": "object"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "grpc.development.echo",
        "In-process gRPC development echo. Remote gRPC channels are not implemented.",
        "grpc.invoke",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.GRPC,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "service": {"type": "string", "minLength": 1, "maxLength": 200},
                "method": {"type": "string", "minLength": 1, "maxLength": 200},
                "message": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["protocol", "adapter", "service", "method", "echo", "run_id"],
            "properties": {
                "protocol": {"const": "grpc"},
                "adapter": {"const": "in-process"},
                "service": {"type": "string"},
                "method": {"type": "string"},
                "echo": {"type": "object"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "workflow.development.echo",
        "In-process workflow development echo. Remote workflow engines are not implemented.",
        "workflow.invoke",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.WORKFLOW,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workflow": {"type": "string", "minLength": 1, "maxLength": 200},
                "operation": {"type": "string", "minLength": 1, "maxLength": 200},
                "input": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["protocol", "adapter", "workflow", "operation", "echo", "run_id"],
            "properties": {
                "protocol": {"const": "workflow"},
                "adapter": {"const": "in-process"},
                "workflow": {"type": "string"},
                "operation": {"type": "string"},
                "echo": {"type": "object"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "workflow.automation.trigger",
        "In-process Gateway dispatch to AutomationService.trigger_workflow. "
        "Remote workflow engines and nested ANALYSIS dispatch are not implemented.",
        "automation.trigger",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.WORKFLOW,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workflow": {"type": "string", "minLength": 1, "maxLength": 200},
                "operation": {"type": "string", "minLength": 1, "maxLength": 200},
                "input": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": [
                "protocol",
                "adapter",
                "workflow",
                "operation",
                "dispatched",
                "execution_id",
                "status",
                "workflow_id",
                "run_id",
            ],
            "properties": {
                "protocol": {"const": "workflow"},
                "adapter": {"const": "in-process"},
                "workflow": {"type": "string"},
                "operation": {"type": "string"},
                "dispatched": {"const": True},
                "execution_id": {"type": "string"},
                "status": {"type": "string"},
                "workflow_id": {"type": "string"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "agent.development.echo",
        "In-process agent development echo. Nested Harness and remote agent "
        "runtimes are not implemented.",
        "agent.invoke",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.AGENT,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "agent": {"type": "string", "minLength": 1, "maxLength": 200},
                "operation": {"type": "string", "minLength": 1, "maxLength": 200},
                "input": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["protocol", "adapter", "agent", "operation", "echo", "run_id"],
            "properties": {
                "protocol": {"const": "agent"},
                "adapter": {"const": "in-process"},
                "agent": {"type": "string"},
                "operation": {"type": "string"},
                "echo": {"type": "object"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "connector.sdk.echo",
        "In-process Connector SDK development echo. Remote connector loading is not implemented.",
        "connector.sdk.invoke",
        "TOOL",
        RiskLevel.L1,
        CapabilityTransport.INTERNAL,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "operation": {"type": "string", "minLength": 1, "maxLength": 200},
                "arguments": {"type": "object"},
            },
        },
        {
            "type": "object",
            "required": ["protocol", "adapter", "operation", "echo", "run_id"],
            "properties": {
                "protocol": {"const": "connector-sdk"},
                "adapter": {"const": "in-process"},
                "operation": {"type": "string"},
                "echo": {"type": "object"},
                "run_id": {"type": "string"},
            },
        },
    ),
    CapabilitySeed(
        "deployment.list",
        "List deployments",
        "deployment.read",
        "DEPLOYMENT",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _observability_input_schema("deployment.list"),
        _observability_output_schema("deployment.list"),
    ),
    CapabilitySeed(
        "config.get",
        "Read effective configuration",
        "config.read",
        "CONFIG",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _engineering_input_schema("config.get"),
        _engineering_output_schema("config.get"),
    ),
    CapabilitySeed(
        "config.diff",
        "Compare configuration versions",
        "config.read",
        "CONFIG",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _engineering_input_schema("config.diff"),
        _engineering_output_schema("config.diff"),
    ),
    CapabilitySeed(
        "k8s.status",
        "Read Kubernetes workload status",
        "runtime.read",
        "TOOL",
        RiskLevel.L2,
        CapabilityTransport.HTTP,
        _engineering_input_schema("k8s.status"),
        _engineering_output_schema("k8s.status"),
    ),
    CapabilitySeed(
        "action.pr.create",
        "Create a pull request through an approved action provider",
        "action.pr.create",
        "TOOL",
        RiskLevel.L3,
        input_schema=_action_input_schema(
            "GENERATE_PR",
            "EXECUTE",
            target_properties=_REPOSITORY_PROPERTY,
            target_required=["repository"],
            parameter_properties={
                "title": {"type": "string", "minLength": 1, "maxLength": 20_000},
                "head": {"type": "string", "minLength": 1, "maxLength": 20_000},
                "base": {"type": "string", "minLength": 1, "maxLength": 20_000},
                "body": {"type": "string", "maxLength": 20_000},
            },
            parameter_required=["title", "head", "base"],
        ),
        output_schema=_action_output_schema(),
        side_effect=SideEffect.IDEMPOTENT_WRITE,
    ),
    CapabilitySeed(
        "action.pr.close",
        "Close a pull request as an approved compensating action",
        "action.pr.rollback",
        "TOOL",
        RiskLevel.L3,
        input_schema=_action_input_schema(
            "GENERATE_PR",
            "ROLLBACK",
            target_properties=_REPOSITORY_PROPERTY,
            target_required=["repository"],
            parameter_properties={
                "reason": {"type": "string", "minLength": 1, "maxLength": 20_000}
            },
            parameter_required=["reason"],
            rollback=True,
        ),
        output_schema=_action_output_schema(state="closed"),
        side_effect=SideEffect.IDEMPOTENT_WRITE,
    ),
    CapabilitySeed(
        "action.ticket.create",
        "Create a ticket through an approved action provider",
        "action.ticket.create",
        "TOOL",
        RiskLevel.L3,
        input_schema=_action_input_schema(
            "CREATE_TICKET",
            "EXECUTE",
            target_properties=_PROJECT_PROPERTY,
            target_required=["project_key"],
            parameter_properties={
                "summary": {"type": "string", "minLength": 1, "maxLength": 20_000},
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 20_000,
                },
                "issue_type": {"type": "string", "minLength": 1, "maxLength": 200},
            },
            parameter_required=["summary", "description"],
        ),
        output_schema=_action_output_schema(),
        side_effect=SideEffect.IDEMPOTENT_WRITE,
    ),
    CapabilitySeed(
        "action.ticket.close",
        "Close a ticket as an approved compensating action",
        "action.ticket.rollback",
        "TOOL",
        RiskLevel.L3,
        input_schema=_action_input_schema(
            "CREATE_TICKET",
            "ROLLBACK",
            target_properties=_PROJECT_PROPERTY,
            target_required=["project_key"],
            parameter_properties={
                "resolution": {"type": "string", "minLength": 1, "maxLength": 20_000}
            },
            parameter_required=["resolution"],
            rollback=True,
        ),
        output_schema=_action_output_schema(state="closed"),
        side_effect=SideEffect.IDEMPOTENT_WRITE,
    ),
]

_IN_PROCESS_ADAPTER_CAPABILITIES = frozenset(
    {
        "agent.development.echo",
        "connector.sdk.echo",
        "grpc.development.echo",
        "mcp.development.echo",
        "sdk.development.echo",
        "workflow.automation.trigger",
        "workflow.development.echo",
    }
)

_AGENTS: dict[str, dict[str, Any]] = {
    "general-agent": {
        "description": "Primary enterprise assistant and internal route coordinator",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 30,
        "timeout": 300,
        "skills": [
            "knowledge-research",
            "governed-analytics",
            "incident-investigation",
            "report-generation",
        ],
        "capabilities": [
            seed.name
            for seed in _CAPABILITIES
            if seed.side_effect == SideEffect.NONE
            and seed.name not in _IN_PROCESS_ADAPTER_CAPABILITIES
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "memory": {"session": True, "workspace": True},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "knowledge-agent": {
        "description": "Permission-aware enterprise knowledge researcher",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 12,
        "skills": ["knowledge-qa"],
        "capabilities": ["knowledge.search", "document.read"],
        "riskPolicy": {"maxLevel": "L1"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "data-agent": {
        "description": "Governed semantic analytics agent",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 18,
        "skills": ["governed-analytics", "sql-analysis"],
        "capabilities": [
            "metric.describe",
            "schema.search",
            "sql.validate",
            "sql.explain",
            "data.query",
            "data.preview",
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "incident-agent": {
        "description": "Production incident investigator",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 30,
        "skills": ["incident-investigation", "log-analysis", "root-cause-analysis"],
        "capabilities": [
            "metric.query",
            "metric.compare",
            "metric.anomaly",
            "metric.dimension",
            "log.aggregate",
            "log.search",
            "trace.search",
            "deployment.list",
            "deployment.commit",
            "git.diff",
            "config.diff",
            "k8s.status",
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "engineering-agent": {
        "description": "Code and system analysis agent",
        "modelPolicy": {"profile": "coding-high"},
        "maxSteps": 24,
        "skills": ["code-architecture", "code-review"],
        "capabilities": [
            "code.search",
            "code.symbol",
            "code.reference",
            "code.callers",
            "code.callees",
            "git.diff",
            "git.history",
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "analytics-agent": {
        "description": "Business metric analysis and insight agent",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 18,
        "skills": ["business-analysis", "trend-analysis", "funnel-analysis"],
        "capabilities": [
            "metric.describe",
            "metric.query",
            "metric.compare",
            "metric.dimension",
            "data.query",
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "operation-agent": {
        "description": "Read-only runtime and delivery operations analyst",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 24,
        "skills": ["log-analysis"],
        "capabilities": [
            "deployment.list",
            "deployment.commit",
            "config.get",
            "config.diff",
            "k8s.status",
            "log.search",
            "metric.query",
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
    "support-agent": {
        "description": "Permission-aware customer support investigation agent",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 18,
        "skills": ["support-diagnosis"],
        "capabilities": [
            "ticket.search",
            "knowledge.search",
            "document.read",
            "log.search",
            "trace.search",
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "sandbox": dict(_DEFAULT_SANDBOX),
    },
}

_SKILLS: dict[str, dict[str, Any]] = {
    "knowledge-qa": {
        "instructions": [
            "use only DOCUMENT Evidence collected by the current Run",
            "answer only what the authorized evidence supports",
            "attach a citation to every factual claim and preserve document version and chunk",
            "if authorized evidence is missing or insufficient, say unknown and do not guess",
            "never query metrics, SQL, logs, traces, code, tickets, or production resources",
        ],
        "capabilities": ["knowledge.search", "document.read"],
        "requiredEvidence": ["DOCUMENT"],
        "verification": ["citation coverage", "ACL retained", "unknown-safe"],
    },
    "code-architecture": {
        "instructions": [
            "use only CODE Evidence collected by the current Run",
            "resolve symbols, references, and call chains from the authorized Code Graph",
            "relate APIs, services, methods, and SQL table references when present",
            "cite repository, path, symbol, and commit for every factual claim",
            "if authorized code evidence is missing, say unknown and do not guess",
            "never execute repository code or open a write path",
        ],
        "capabilities": [
            "code.symbol",
            "code.reference",
            "code.callers",
            "code.callees",
            "code.search",
        ],
        "requiredEvidence": ["CODE"],
        "verification": ["symbol lineage", "source version", "question coverage"],
    },
    "knowledge-research": {
        "instructions": [
            "retrieve only authorized sources",
            "cite source and version",
            "separate unsupported statements",
        ],
        "capabilities": ["knowledge.search", "document.read"],
        "requiredEvidence": ["DOCUMENT"],
        "verification": ["citation coverage", "ACL retained"],
    },
    "governed-analytics": {
        "instructions": [
            "resolve governed metric",
            "build logical plan",
            "validate AST",
            "execute read-only query",
            "produce a table or trend chart artifact from DATA Evidence",
            "for decline questions, segment only by governed dimensions and never "
            "inspect logs or traces",
            "explain metric definition",
        ],
        "capabilities": ["metric.describe", "sql.validate", "sql.explain", "data.query"],
        "requiredEvidence": ["DATA"],
        "verification": ["metric definition", "query bounded", "result cited"],
    },
    "incident-investigation": {
        "instructions": [
            "establish a metric baseline before making any causal statement",
            "locate the bounded anomaly window",
            "drill down only through authorized metric dimensions",
            "correlate deployments and commits inside the same time window",
            "inspect authorized logs and traces after the change correlation",
            "produce ranked candidate root causes, never an automatic conclusion",
            "bind every candidate claim to at least two distinct Evidence types",
            "test alternatives and preserve unresolved conflicts",
            "never repair, restart, reconfigure, or write to production",
        ],
        "capabilities": [
            "metric.query",
            "metric.compare",
            "metric.anomaly",
            "metric.dimension",
            "log.aggregate",
            "trace.search",
            "deployment.list",
            "git.diff",
            "config.diff",
        ],
        "requiredEvidence": ["METRIC", "DEPLOYMENT", "LOG"],
        "optionalEvidence": ["TRACE", "CODE", "GIT", "CONFIG"],
        "verification": [
            "temporal consistency",
            "cross-type evidence",
            "conflicting causes",
            "evidence coverage",
        ],
    },
    "sql-analysis": {
        "instructions": [
            "resolve only governed metrics, tables, and dimensions",
            "compile a bounded read-only logical plan before any SQL is considered",
            "validate the AST against the SQL policy; reject writes, DDL, and unbounded scans",
            "explain the query with cited metric definitions, never raw warehouse credentials",
            "if the metric or table is unauthorized or unresolved, say unknown and do not guess",
            "never inspect logs, traces, tickets, or production hosts to answer a SQL question",
        ],
        "capabilities": [
            "metric.describe",
            "schema.search",
            "sql.validate",
            "sql.explain",
            "data.query",
        ],
        "requiredEvidence": ["DATA"],
        "optionalEvidence": ["SQL"],
        "verification": ["metric definition", "sql validated", "query bounded", "result cited"],
    },
    "business-analysis": {
        "instructions": [
            "answer business questions only from governed metrics and authorized DATA Evidence",
            "describe the metric definition, grain, and time window before any insight",
            "segment only by governed dimensions; do not invent slices or join unauthorized tables",
            "separate observed change from unsupported causal claims",
            "produce a table or trend artifact from cited DATA Evidence",
            "never query logs, traces, code, tickets, or production resources",
        ],
        "capabilities": [
            "metric.describe",
            "metric.query",
            "metric.compare",
            "metric.dimension",
            "data.query",
        ],
        "requiredEvidence": ["DATA"],
        "optionalEvidence": ["METRIC"],
        "verification": ["metric definition", "dimension ACL", "result cited", "unknown-safe"],
    },
    "trend-analysis": {
        "instructions": [
            "compute trends only on governed metrics inside the requested time window",
            "use comparison periods that the semantic catalog allows, never ad-hoc calendars",
            "show direction, magnitude, and grain; do not infer root cause from a trend alone",
            "cite the metric version and the DATA Evidence rows that support the series",
            "if the window or metric is missing, say unknown",
            "never inspect logs, traces, deployments, or code to decorate a trend",
        ],
        "capabilities": ["metric.describe", "metric.query", "metric.compare", "data.query"],
        "requiredEvidence": ["DATA"],
        "optionalEvidence": ["METRIC"],
        "verification": ["time window bounded", "metric definition", "series cited"],
    },
    "funnel-analysis": {
        "instructions": [
            "resolve each funnel step to a governed metric or dimension",
            "compute conversion only from authorized DATA Evidence in one consistent window",
            "do not stitch funnels across unauthorized events, logs, or product analytics pixels",
            "report drop-off as an observation, not a causal diagnosis",
            "cite step definitions and the result rows used for each conversion",
            "never open a write path or query production operational systems",
        ],
        "capabilities": ["metric.describe", "metric.query", "metric.dimension", "data.query"],
        "requiredEvidence": ["DATA"],
        "optionalEvidence": ["METRIC"],
        "verification": ["step definitions", "conversion cited", "window consistent"],
    },
    "code-review": {
        "instructions": [
            "review only authorized CODE and GIT Evidence from the current Run",
            "resolve symbols and diffs from the Code Graph and git.* reads, "
            "never by executing the repo",
            "cite repository, path, symbol, and commit for every finding",
            "distinguish style notes from defects that the evidence actually shows",
            "if authorized evidence is missing, say unknown and do not guess",
            "never apply patches, open pull requests, or write to any environment",
        ],
        "capabilities": [
            "code.symbol",
            "code.reference",
            "code.callers",
            "code.callees",
            "code.search",
            "git.diff",
            "git.history",
        ],
        "requiredEvidence": ["CODE"],
        "optionalEvidence": ["GIT"],
        "verification": ["symbol lineage", "source version", "finding cited", "no write path"],
    },
    "log-analysis": {
        "instructions": [
            "search only authorized, time-bounded production logs and related read-only status",
            "redaction and ACL are applied by the Capability Gateway, not by prompt instructions",
            "correlate log volume with metric and deployment Evidence when those types are present",
            "never treat a single log line as a root cause",
            "never restart, scale, reconfigure, or otherwise write to production",
            "if the window or service is unauthorized, say unknown",
        ],
        "capabilities": [
            "log.search",
            "log.aggregate",
            "metric.query",
            "deployment.list",
            "k8s.status",
            "config.get",
        ],
        "requiredEvidence": ["LOG"],
        "optionalEvidence": ["METRIC", "DEPLOYMENT", "CONFIG", "TOOL"],
        "verification": ["time window bounded", "ACL retained", "no write path"],
    },
    "root-cause-analysis": {
        "instructions": [
            "establish a metric baseline before ranking any cause",
            "require at least two distinct Evidence types for every causal candidate",
            "correlate deployments, commits, configuration, logs, and traces in one window",
            "keep unresolved conflicts attached; never collapse them into a single conclusion",
            "produce at most three ranked candidates, never an automatic repair",
            "never restart, reconfigure, deploy, or write to production",
        ],
        "capabilities": [
            "metric.query",
            "metric.compare",
            "metric.anomaly",
            "metric.dimension",
            "log.aggregate",
            "trace.search",
            "deployment.list",
            "git.diff",
            "config.diff",
            "k8s.status",
        ],
        "requiredEvidence": ["METRIC", "DEPLOYMENT", "LOG"],
        "optionalEvidence": ["TRACE", "CODE", "GIT", "CONFIG"],
        "verification": [
            "temporal consistency",
            "cross-type evidence",
            "conflicting causes",
            "evidence coverage",
        ],
    },
    "report-generation": {
        "instructions": [
            "assemble a report only from Evidence collected by the current Run",
            "every factual paragraph must cite Evidence IDs already on the Run",
            "label missing types as unknown instead of interpolating from memory or conversation",
            "keep DATA, DOCUMENT, CODE, and operational Evidence in separate cited sections",
            "never query a new production system solely to fill a report template",
            "never include credentials, raw PII, or unredacted payloads",
        ],
        "capabilities": ["knowledge.search", "metric.describe", "data.query"],
        "requiredEvidence": ["DOCUMENT"],
        "optionalEvidence": ["DATA", "METRIC", "CODE"],
        "verification": ["citation coverage", "unknown-safe", "no extra production access"],
    },
    "support-diagnosis": {
        "instructions": [
            "diagnose customer issues from authorized tickets and knowledge only",
            "search tickets as DOCUMENT Evidence with the same ACL as the knowledge index",
            "cite ticket source, title, version, and chunk for every procedural claim",
            "use knowledge articles to explain policy; do not invent refund or account actions",
            "logs and traces are optional and read-only; skip them unless the "
            "question names an error",
            "never create, close, or comment on tickets, never query SQL, and "
            "never write to production",
            "if authorized ticket or policy evidence is missing, say unknown",
        ],
        "capabilities": [
            "ticket.search",
            "knowledge.search",
            "document.read",
            "log.search",
            "trace.search",
        ],
        "requiredEvidence": ["DOCUMENT"],
        "optionalEvidence": ["LOG", "TRACE"],
        "verification": ["citation coverage", "ACL retained", "no write path", "unknown-safe"],
    },
}


async def bootstrap_builtin_registry(
    session: AsyncSession, organization_id: UUID, actor_id: UUID
) -> None:
    now = utc_now()
    agent_specs, skill_specs = load_registry_specs(_AGENTS, _SKILLS)
    profile_requirements = {
        "reasoning-high": {"profile": "reasoning-high", "capabilities": ["chat"]},
        "coding-high": {"profile": "coding-high", "capabilities": ["chat"]},
        "fast": {"profile": "fast", "capabilities": ["chat"]},
        "private": {"profile": "private", "capabilities": ["chat"], "private": True},
        "vision": {"profile": "vision", "capabilities": ["chat", "vision"]},
    }
    for name, requirements in profile_requirements.items():
        existing = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.organization_id == organization_id,
                ModelProfile.name == name,
            )
        )
        if existing is None:
            session.add(
                ModelProfile(
                    organization_id=organization_id,
                    name=name,
                    requirements=requirements,
                    routing_policy={"fallback": True},
                    enabled=True,
                )
            )

    prompt_definition = await session.scalar(
        select(PromptDefinition).where(
            PromptDefinition.organization_id == organization_id,
            PromptDefinition.name == SYSTEM_POLICY_PROMPT_NAME,
        )
    )
    if prompt_definition is None:
        prompt_definition = PromptDefinition(
            organization_id=organization_id,
            name=SYSTEM_POLICY_PROMPT_NAME,
            display_name="Obsion system policy",
            description="Pinned platform policy for Context Builder SYSTEM segments",
            status=RegistryStatus.ACTIVE,
        )
        session.add(prompt_definition)
        await session.flush()
    if (
        await session.scalar(
            select(PromptVersion).where(
                PromptVersion.prompt_id == prompt_definition.id,
                PromptVersion.version == 1,
            )
        )
        is None
    ):
        session.add(
            PromptVersion(
                organization_id=organization_id,
                prompt_id=prompt_definition.id,
                version=1,
                template=DEFAULT_SYSTEM_POLICY_TEMPLATE,
                variables_schema=dict(DEFAULT_SYSTEM_POLICY_SCHEMA),
                checksum_sha256=prompt_checksum(
                    DEFAULT_SYSTEM_POLICY_TEMPLATE, DEFAULT_SYSTEM_POLICY_SCHEMA
                ),
                created_by=actor_id,
                created_at=now,
            )
        )

    knowledge_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-knowledge-index",
        )
    )
    if knowledge_connector is None:
        knowledge_connector = Connector(
            organization_id=organization_id,
            name="obsion-knowledge-index",
            connector_type="knowledge-index",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={},
            declared_grants=["knowledge.read"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(knowledge_connector)
        await session.flush()

    feishu_docs_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-feishu-docs",
        )
    )
    if feishu_docs_connector is None:
        feishu_docs_connector = Connector(
            organization_id=organization_id,
            name="obsion-feishu-docs",
            connector_type="feishu-docs",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            endpoint="https://open.feishu.cn",
            configuration={
                "protocol": "feishu.docs.v1",
                "app_id_env": "OBSION_FEISHU_APP_ID",
                "rate_limit_per_minute": 60,
                "knowledge_sync_budget": {"max_pages": 20, "max_nodes": 200, "max_depth": 8},
            },
            credential_ref="env://OBSION_FEISHU_APP_SECRET",
            declared_grants=["knowledge.write", "knowledge.read"],
            allowed_egress=["https://open.feishu.cn"],
            last_health={"status": "ready"},
        )
        session.add(feishu_docs_connector)
        await session.flush()

    dingtalk_docs_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-dingtalk-docs",
        )
    )
    if dingtalk_docs_connector is None:
        dingtalk_docs_connector = Connector(
            organization_id=organization_id,
            name="obsion-dingtalk-docs",
            connector_type="dingtalk-docs",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            endpoint="https://api.dingtalk.com",
            configuration={
                "protocol": "dingtalk.docs.v1",
                "app_key_env": "OBSION_DINGTALK_APP_KEY",
                "rate_limit_per_minute": 60,
                "knowledge_sync_budget": {"max_pages": 20, "max_nodes": 200, "max_depth": 8},
            },
            credential_ref="env://OBSION_DINGTALK_APP_SECRET",
            declared_grants=["knowledge.write", "knowledge.read"],
            allowed_egress=["https://api.dingtalk.com"],
            last_health={"status": "ready"},
        )
        session.add(dingtalk_docs_connector)
        await session.flush()

    wecom_docs_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-wecom-docs",
        )
    )
    if wecom_docs_connector is None:
        wecom_docs_connector = Connector(
            organization_id=organization_id,
            name="obsion-wecom-docs",
            connector_type="wecom-docs",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            endpoint="https://qyapi.weixin.qq.com",
            configuration={
                "protocol": "wecom.docs.v1",
                "corp_id_env": "OBSION_WECOM_CORP_ID",
                "rate_limit_per_minute": 60,
                "knowledge_sync_budget": {"max_pages": 20, "max_nodes": 200, "max_depth": 8},
            },
            credential_ref="env://OBSION_WECOM_CORP_SECRET",
            declared_grants=["knowledge.write", "knowledge.read"],
            allowed_egress=["https://qyapi.weixin.qq.com"],
            last_health={"status": "ready"},
        )
        session.add(wecom_docs_connector)
        await session.flush()

    confluence_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-confluence",
        )
    )
    if confluence_connector is None:
        confluence_connector = Connector(
            organization_id=organization_id,
            name="obsion-confluence",
            connector_type="confluence",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            endpoint="https://example.atlassian.net",
            configuration={
                "protocol": "confluence.cloud.v2",
                "email_env": "OBSION_CONFLUENCE_EMAIL",
                "site_host": "example.atlassian.net",
                "rate_limit_per_minute": 60,
                "knowledge_sync_budget": {"max_pages": 20, "max_nodes": 200, "max_depth": 8},
            },
            credential_ref="env://OBSION_CONFLUENCE_API_TOKEN",
            declared_grants=["knowledge.write", "knowledge.read"],
            allowed_egress=["https://example.atlassian.net"],
            last_health={"status": "ready"},
        )
        session.add(confluence_connector)
        await session.flush()

    code_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-code-index",
        )
    )
    if code_connector is None:
        code_connector = Connector(
            organization_id=organization_id,
            name="obsion-code-index",
            connector_type="code-index",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={},
            declared_grants=["code.read"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(code_connector)
        await session.flush()

    mcp_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-mcp-development",
        )
    )
    if mcp_connector is None:
        mcp_connector = Connector(
            organization_id=organization_id,
            name="obsion-mcp-development",
            connector_type="mcp-development",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={"tool": "obsion.echo"},
            declared_grants=["mcp.invoke"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(mcp_connector)
        await session.flush()

    sdk_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-sdk-development",
        )
    )
    if sdk_connector is None:
        sdk_connector = Connector(
            organization_id=organization_id,
            name="obsion-sdk-development",
            connector_type="sdk-development",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={"sdk": "obsion.development", "method": "echo"},
            declared_grants=["sdk.invoke"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(sdk_connector)
        await session.flush()

    grpc_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-grpc-development",
        )
    )
    if grpc_connector is None:
        grpc_connector = Connector(
            organization_id=organization_id,
            name="obsion-grpc-development",
            connector_type="grpc-development",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={"service": "obsion.development.Echo", "method": "Ping"},
            declared_grants=["grpc.invoke"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(grpc_connector)
        await session.flush()

    workflow_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-workflow-development",
        )
    )
    if workflow_connector is None:
        workflow_connector = Connector(
            organization_id=organization_id,
            name="obsion-workflow-development",
            connector_type="workflow-development",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={"workflow": "obsion.development", "operation": "echo"},
            declared_grants=["workflow.invoke"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(workflow_connector)
        await session.flush()

    agent_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-agent-development",
        )
    )
    if agent_connector is None:
        agent_connector = Connector(
            organization_id=organization_id,
            name="obsion-agent-development",
            connector_type="agent-development",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration={"agent": "obsion.development", "operation": "echo"},
            declared_grants=["agent.invoke"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(agent_connector)
        await session.flush()

    connector_sdk_connector = await session.scalar(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.name == "obsion-connector-sdk-development",
        )
    )
    if connector_sdk_connector is None:
        connector_sdk_connector = Connector(
            organization_id=organization_id,
            name="obsion-connector-sdk-development",
            connector_type="connector-sdk-development",
            status=ConnectorStatus.ACTIVE,
            environment="development",
            configuration=development_plugin_configuration(),
            declared_grants=["connector.sdk.invoke"],
            allowed_egress=[],
            last_health={"status": "ready"},
        )
        session.add(connector_sdk_connector)
        await session.flush()
    else:
        configuration = (
            dict(connector_sdk_connector.configuration)
            if isinstance(connector_sdk_connector.configuration, dict)
            else {}
        )
        if "plugin" not in configuration:
            connector_sdk_connector.configuration = development_plugin_configuration(configuration)

    for seed in _CAPABILITIES:
        definition = await session.scalar(
            select(CapabilityDefinition).where(
                CapabilityDefinition.organization_id == organization_id,
                CapabilityDefinition.name == seed.name,
            )
        )
        if definition is None:
            definition = CapabilityDefinition(
                organization_id=organization_id,
                name=seed.name,
                display_name=seed.name,
                description=seed.description,
                status=RegistryStatus.ACTIVE,
            )
            session.add(definition)
            await session.flush()
        input_schema = seed.input_schema or {"type": "object"}
        output_schema = seed.output_schema or {"type": "object"}
        descriptor = {
            "name": seed.name,
            "transport": seed.transport,
            "risk": seed.risk,
            "permission": seed.permission,
            "input": input_schema,
            "output": output_schema,
            "side_effect": seed.side_effect,
        }
        checksum = _checksum(descriptor)
        versions = list(
            await session.scalars(
                select(CapabilityVersion)
                .where(CapabilityVersion.capability_id == definition.id)
                .order_by(CapabilityVersion.version.desc())
            )
        )
        version = next(
            (candidate for candidate in versions if candidate.checksum_sha256 == checksum),
            None,
        )
        if version is None:
            version = CapabilityVersion(
                organization_id=organization_id,
                capability_id=definition.id,
                version=versions[0].version + 1 if versions else 1,
                transport=seed.transport,
                risk_level=seed.risk,
                side_effect=seed.side_effect,
                permission_action=seed.permission,
                input_schema=input_schema,
                output_schema=output_schema,
                evidence_mapping={"type": seed.evidence_type, "confidence": 1.0},
                timeout_seconds=30,
                data_classification=Classification.INTERNAL,
                checksum_sha256=checksum,
                created_at=now,
            )
            session.add(version)
            await session.flush()
        CapabilityDescriptor.from_models(definition, version)
        if seed.name in {"knowledge.search", "ticket.search"}:
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == knowledge_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=knowledge_connector.id,
                        environment="development",
                        resource_selector={"index": "organization"},
                        enabled=True,
                    )
                )
        if seed.name in {
            "knowledge.ingest",
            "knowledge.sync",
            *VENDOR_KNOWLEDGE_BROWSE_OPERATIONS,
        }:
            for target, source in (
                (feishu_docs_connector, "feishu"),
                (dingtalk_docs_connector, "dingtalk"),
                (wecom_docs_connector, "wecom"),
                (confluence_connector, "confluence"),
            ):
                binding = await session.scalar(
                    select(CapabilityBinding).where(
                        CapabilityBinding.capability_version_id == version.id,
                        CapabilityBinding.connector_id == target.id,
                        CapabilityBinding.environment == "development",
                    )
                )
                if binding is None:
                    session.add(
                        CapabilityBinding(
                            organization_id=organization_id,
                            capability_version_id=version.id,
                            connector_id=target.id,
                            environment="development",
                            resource_selector={"index": "organization", "source": source},
                            enabled=True,
                        )
                    )
        if seed.name in {"code.symbol", "code.reference", "code.callers", "code.callees"}:
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == code_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=code_connector.id,
                        environment="development",
                        resource_selector={"index": "organization"},
                        enabled=True,
                    )
                )
        if seed.name == "mcp.development.echo":
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == mcp_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=mcp_connector.id,
                        environment="development",
                        resource_selector={},
                        enabled=True,
                    )
                )
        if seed.name == "sdk.development.echo":
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == sdk_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=sdk_connector.id,
                        environment="development",
                        resource_selector={},
                        enabled=True,
                    )
                )
        if seed.name == "grpc.development.echo":
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == grpc_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=grpc_connector.id,
                        environment="development",
                        resource_selector={},
                        enabled=True,
                    )
                )
        if seed.name == "workflow.development.echo":
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == workflow_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=workflow_connector.id,
                        environment="development",
                        resource_selector={},
                        enabled=True,
                    )
                )
        if seed.name == "agent.development.echo":
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == agent_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=agent_connector.id,
                        environment="development",
                        resource_selector={},
                        enabled=True,
                    )
                )
        if seed.name == "connector.sdk.echo":
            binding = await session.scalar(
                select(CapabilityBinding).where(
                    CapabilityBinding.capability_version_id == version.id,
                    CapabilityBinding.connector_id == connector_sdk_connector.id,
                    CapabilityBinding.environment == "development",
                )
            )
            if binding is None:
                session.add(
                    CapabilityBinding(
                        organization_id=organization_id,
                        capability_version_id=version.id,
                        connector_id=connector_sdk_connector.id,
                        environment="development",
                        resource_selector={},
                        enabled=True,
                    )
                )

    for name, spec in agent_specs.items():
        AgentSpec.from_dict(spec, source=f"builtin Agent {name}")
        agent_definition = await session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.organization_id == organization_id,
                AgentDefinition.name == name,
            )
        )
        if agent_definition is None:
            agent_definition = AgentDefinition(
                organization_id=organization_id,
                name=name,
                display_name=name.replace("-", " ").title(),
                description=spec["description"],
                status=RegistryStatus.ACTIVE,
                active_version=1,
            )
            session.add(agent_definition)
            await session.flush()
        if (
            agent_definition.active_version is None
            and agent_definition.status == RegistryStatus.ACTIVE
        ):
            agent_definition.active_version = 1
        if (
            await session.scalar(
                select(AgentVersion).where(
                    AgentVersion.agent_id == agent_definition.id, AgentVersion.version == 1
                )
            )
            is None
        ):
            session.add(
                AgentVersion(
                    organization_id=organization_id,
                    agent_id=agent_definition.id,
                    version=1,
                    spec=spec,
                    checksum_sha256=_checksum(spec),
                    created_by=actor_id,
                    created_at=now,
                    promoted_at=now,
                )
            )

    for name, spec in skill_specs.items():
        skill_definition = await session.scalar(
            select(SkillDefinition).where(
                SkillDefinition.organization_id == organization_id,
                SkillDefinition.name == name,
            )
        )
        if skill_definition is None:
            skill_definition = SkillDefinition(
                organization_id=organization_id,
                name=name,
                display_name=name.replace("-", " ").title(),
                description=f"Governed {name.replace('-', ' ')} procedure",
                status=RegistryStatus.ACTIVE,
                active_version=1,
            )
            session.add(skill_definition)
            await session.flush()
        if (
            skill_definition.active_version is None
            and skill_definition.status == RegistryStatus.ACTIVE
        ):
            skill_definition.active_version = 1
        if (
            await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.skill_id == skill_definition.id, SkillVersion.version == 1
                )
            )
            is None
        ):
            session.add(
                SkillVersion(
                    organization_id=organization_id,
                    skill_id=skill_definition.id,
                    version=1,
                    spec=spec,
                    checksum_sha256=_checksum(spec),
                    created_by=actor_id,
                    created_at=now,
                )
            )
