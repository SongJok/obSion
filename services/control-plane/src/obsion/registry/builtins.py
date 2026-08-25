import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.time import utc_now
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    ModelProfile,
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
from obsion.registry.manifests import load_registry_specs


def _checksum(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


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


_CAPABILITIES = [
    CapabilitySeed("code.search", "Search indexed source code", "code.read", "CODE"),
    CapabilitySeed("code.symbol", "Resolve a source symbol", "code.read", "CODE"),
    CapabilitySeed("code.reference", "Find symbol references", "code.read", "CODE"),
    CapabilitySeed("git.commit", "Read commit metadata", "code.read", "CODE"),
    CapabilitySeed("git.diff", "Read an immutable Git diff", "code.read", "CODE"),
    CapabilitySeed("git.blame", "Read Git blame attribution", "code.read", "CODE"),
    CapabilitySeed("git.history", "Read repository history", "code.read", "CODE"),
    CapabilitySeed(
        "deployment.commit",
        "Resolve deployed commit lineage",
        "deployment.read",
        "DEPLOYMENT",
        RiskLevel.L2,
    ),
    CapabilitySeed("log.search", "Search authorized logs", "logs.read", "LOG", RiskLevel.L2),
    CapabilitySeed("log.error", "Search error events", "logs.read", "LOG", RiskLevel.L2),
    CapabilitySeed("log.aggregate", "Aggregate log events", "logs.read", "LOG", RiskLevel.L2),
    CapabilitySeed(
        "trace.search", "Search distributed traces", "traces.read", "TRACE", RiskLevel.L2
    ),
    CapabilitySeed(
        "trace.timeline", "Build a trace timeline", "traces.read", "TRACE", RiskLevel.L2
    ),
    CapabilitySeed("metric.query", "Query a governed metric", "metrics.read", "METRIC"),
    CapabilitySeed("metric.compare", "Compare metric periods", "metrics.read", "METRIC"),
    CapabilitySeed("metric.anomaly", "Detect metric anomalies", "metrics.read", "METRIC"),
    CapabilitySeed("metric.dimension", "Drill down metric dimensions", "metrics.read", "METRIC"),
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
        "document.read", "Read an authorized document version", "knowledge.read", "DOCUMENT"
    ),
    CapabilitySeed(
        "policy.search", "Search policies available to a principal", "policy.read", "DOCUMENT"
    ),
    CapabilitySeed("ticket.search", "Search authorized tickets", "tickets.read", "DOCUMENT"),
    CapabilitySeed(
        "deployment.list", "List deployments", "deployment.read", "DEPLOYMENT", RiskLevel.L2
    ),
    CapabilitySeed(
        "config.get", "Read effective configuration", "config.read", "CONFIG", RiskLevel.L2
    ),
    CapabilitySeed(
        "config.diff", "Compare configuration versions", "config.read", "CONFIG", RiskLevel.L2
    ),
    CapabilitySeed(
        "k8s.status", "Read Kubernetes workload status", "runtime.read", "TOOL", RiskLevel.L2
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

_AGENTS: dict[str, dict[str, Any]] = {
    "general-agent": {
        "description": "Primary enterprise assistant and internal route coordinator",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 30,
        "timeout": 300,
        "skills": ["knowledge-research", "governed-analytics", "incident-investigation"],
        "capabilities": [
            seed.name for seed in _CAPABILITIES if seed.side_effect == SideEffect.NONE
        ],
        "riskPolicy": {"maxLevel": "L2"},
        "memory": {"session": True, "workspace": True},
        "sandbox": {"enabled": True, "network": "gateway-only"},
    },
    "knowledge-agent": {
        "description": "Permission-aware enterprise knowledge researcher",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 12,
        "capabilities": ["knowledge.search", "document.read", "policy.search", "ticket.search"],
        "riskPolicy": {"maxLevel": "L1"},
    },
    "data-agent": {
        "description": "Governed semantic analytics agent",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 18,
        "capabilities": [
            "metric.describe",
            "schema.search",
            "sql.validate",
            "data.query",
            "data.preview",
        ],
        "riskPolicy": {"maxLevel": "L2"},
    },
    "incident-agent": {
        "description": "Production incident investigator",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 30,
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
    },
    "engineering-agent": {
        "description": "Code and system analysis agent",
        "modelPolicy": {"profile": "coding-high"},
        "maxSteps": 24,
        "capabilities": ["code.search", "code.symbol", "code.reference", "git.diff", "git.history"],
        "riskPolicy": {"maxLevel": "L2"},
    },
    "analytics-agent": {
        "description": "Business metric analysis and insight agent",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 18,
        "capabilities": [
            "metric.describe",
            "metric.query",
            "metric.compare",
            "metric.dimension",
            "data.query",
        ],
        "riskPolicy": {"maxLevel": "L2"},
    },
    "operation-agent": {
        "description": "Read-only runtime and delivery operations analyst",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 24,
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
    },
    "support-agent": {
        "description": "Permission-aware customer support investigation agent",
        "modelPolicy": {"profile": "reasoning-high"},
        "maxSteps": 18,
        "capabilities": [
            "ticket.search",
            "knowledge.search",
            "document.read",
            "log.search",
            "trace.search",
        ],
        "riskPolicy": {"maxLevel": "L2"},
    },
}

_SKILLS: dict[str, dict[str, Any]] = {
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
            "explain metric definition",
        ],
        "capabilities": ["metric.describe", "sql.validate", "data.query"],
        "requiredEvidence": ["DATA"],
        "verification": ["metric definition", "query bounded", "result cited"],
    },
    "incident-investigation": {
        "instructions": [
            "establish baseline",
            "locate anomaly",
            "segment",
            "correlate changes",
            "inspect logs and traces",
            "test alternatives",
        ],
        "capabilities": [
            "metric.anomaly",
            "metric.dimension",
            "log.aggregate",
            "trace.search",
            "deployment.list",
            "git.diff",
            "config.diff",
        ],
        "requiredEvidence": ["METRIC", "DEPLOYMENT", "LOG"],
        "optionalEvidence": ["TRACE", "CODE", "CONFIG"],
        "verification": ["temporal consistency", "conflicting causes", "evidence coverage"],
    },
}


async def bootstrap_builtin_registry(
    session: AsyncSession, organization_id: UUID, actor_id: UUID
) -> None:
    now = utc_now()
    agent_specs, skill_specs = load_registry_specs(_AGENTS, _SKILLS)
    for name in ["reasoning-high", "coding-high", "fast", "private", "vision"]:
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
                    requirements={"profile": name},
                    routing_policy={"fallback": False},
                    enabled=True,
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
        if seed.name == "knowledge.search":
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

    for name, spec in agent_specs.items():
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
            )
            session.add(agent_definition)
            await session.flush()
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
            )
            session.add(skill_definition)
            await session.flush()
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
