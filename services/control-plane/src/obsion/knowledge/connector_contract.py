"""Shared Vendor Knowledge connector contracts: sync budget, provenance, REST rate limit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.rate_limit import CapabilityRateLimiter, RateLimitUnavailable
from obsion.common.errors import ObsionError, ValidationError
from obsion.db.models import CapabilityBinding, CapabilityDefinition, CapabilityVersion, Connector
from obsion.security.identity import Principal

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_NODES = 200
DEFAULT_MAX_DEPTH = 8


@dataclass(frozen=True, slots=True)
class KnowledgeConnectorBudget:
    max_pages: int = DEFAULT_MAX_PAGES
    max_nodes: int = DEFAULT_MAX_NODES
    max_depth: int = DEFAULT_MAX_DEPTH

    @classmethod
    def from_connector(cls, connector: Connector) -> KnowledgeConnectorBudget:
        configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
        raw = configuration.get("knowledge_sync_budget")
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValidationError(
                "knowledge_sync_budget_invalid",
                "knowledge_sync_budget must be a JSON object",
            )
        return cls(
            max_pages=_positive_int(raw.get("max_pages"), "max_pages", DEFAULT_MAX_PAGES),
            max_nodes=_positive_int(raw.get("max_nodes"), "max_nodes", DEFAULT_MAX_NODES),
            max_depth=_non_negative_int(raw.get("max_depth"), "max_depth", DEFAULT_MAX_DEPTH),
        )


@dataclass(frozen=True, slots=True)
class SyncBudgetState:
    pages_used: int
    nodes_used: int
    depth_used: int
    exhausted: bool
    exhausted_dimension: str | None
    limits: KnowledgeConnectorBudget

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_used": self.pages_used,
            "nodes_used": self.nodes_used,
            "depth_used": self.depth_used,
            "exhausted": self.exhausted,
            "exhausted_dimension": self.exhausted_dimension,
            "limits": {
                "max_pages": self.limits.max_pages,
                "max_nodes": self.limits.max_nodes,
                "max_depth": self.limits.max_depth,
            },
        }


class SyncBudgetTracker:
    """Fail-closed sync walk budget. Exhaustion raises; never silently truncates."""

    def __init__(self, budget: KnowledgeConnectorBudget) -> None:
        self.budget = budget
        self._pages_used = 0
        self._nodes_used = 0
        self._depth_used = 0
        self._exhausted = False
        self._exhausted_dimension: str | None = None

    def consume_page(self) -> None:
        if self._pages_used >= self.budget.max_pages:
            self._fail("pages", self.budget.max_pages, self._pages_used)
        self._pages_used += 1

    def consume_node(self) -> None:
        if self._nodes_used >= self.budget.max_nodes:
            self._fail("nodes", self.budget.max_nodes, self._nodes_used)
        self._nodes_used += 1

    def enter_depth(self, depth: int) -> None:
        if depth < 0:
            raise ValidationError(
                "knowledge_sync_budget_invalid",
                "Sync walk depth cannot be negative",
            )
        if depth > self.budget.max_depth:
            self._fail("depth", self.budget.max_depth, depth)
        self._depth_used = max(self._depth_used, depth)

    def snapshot(self) -> SyncBudgetState:
        return SyncBudgetState(
            pages_used=self._pages_used,
            nodes_used=self._nodes_used,
            depth_used=self._depth_used,
            exhausted=self._exhausted,
            exhausted_dimension=self._exhausted_dimension,
            limits=self.budget,
        )

    def _fail(self, dimension: str, limit: int, used: int) -> None:
        self._exhausted = True
        self._exhausted_dimension = dimension
        raise ValidationError(
            "knowledge_sync_budget_exceeded",
            f"Vendor knowledge sync exceeded the {dimension} budget",
            dimension=dimension,
            limit=limit,
            used=used,
        )


@dataclass(frozen=True, slots=True)
class VendorKnowledgeProvenance:
    source: str
    external_id: str
    revision_id: str | None
    connector_name: str
    connector_id: str | None
    operation: str
    sync_scope_id: str | None = None

    def as_version_metadata(self) -> dict[str, Any]:
        return {
            "vendor_source": self.source,
            "external_id": self.external_id,
            "revision_id": self.revision_id,
            "connector_name": self.connector_name,
            "connector_id": self.connector_id,
            "operation": self.operation,
            "sync_scope_id": self.sync_scope_id,
        }


def attach_ingest_provenance(
    *,
    extra_metadata: dict[str, Any] | None,
    provenance: VendorKnowledgeProvenance,
) -> dict[str, Any]:
    merged = dict(extra_metadata or {})
    merged.update(provenance.as_version_metadata())
    return merged


def attach_sync_result_envelope(
    *,
    result: dict[str, Any],
    budget: SyncBudgetState,
    provenance: VendorKnowledgeProvenance,
) -> dict[str, Any]:
    envelope = dict(result)
    envelope["budget"] = budget.as_dict()
    envelope["provenance"] = provenance.as_version_metadata()
    return envelope


def provenance_fields_from_version(
    *,
    source: str,
    external_id: str,
    metadata: dict[str, Any] | None,
) -> dict[str, str | None]:
    meta = metadata if isinstance(metadata, dict) else {}
    revision = meta.get("revision_id")
    if revision is None:
        for key in (
            f"{source}_revision_id",
            "feishu_revision_id",
            "dingtalk_revision_id",
            "wecom_revision_id",
            "confluence_version",
        ):
            if meta.get(key) is not None:
                revision = meta.get(key)
                break
    return {
        "external_id": external_id,
        "revision_id": str(revision) if revision is not None else None,
        "connector_name": (
            str(meta["connector_name"]) if isinstance(meta.get("connector_name"), str) else None
        ),
        "operation": str(meta["operation"]) if isinstance(meta.get("operation"), str) else None,
    }


async def lookup_bound_capability_version_id(
    session: AsyncSession,
    *,
    organization_id: UUID,
    connector_id: UUID,
    capability_name: str,
) -> UUID | None:
    row = await session.scalar(
        select(CapabilityVersion.id)
        .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.capability_id)
        .join(
            CapabilityBinding,
            CapabilityBinding.capability_version_id == CapabilityVersion.id,
        )
        .where(
            CapabilityDefinition.name == capability_name,
            CapabilityVersion.organization_id == organization_id,
            CapabilityBinding.organization_id == organization_id,
            CapabilityBinding.connector_id == connector_id,
            CapabilityBinding.enabled.is_(True),
        )
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )
    return row


async def enforce_knowledge_capability_rate_limit(
    session: AsyncSession,
    *,
    rate_limiter: CapabilityRateLimiter,
    principal: Principal,
    connector: Connector,
    capability_name: str,
    default_limit: int,
) -> None:
    """Align Operator REST with Capability Gateway rate-limit key semantics."""
    version_id = await lookup_bound_capability_version_id(
        session,
        organization_id=principal.organization_id,
        connector_id=connector.id,
        capability_name=capability_name,
    )
    rate_key = ":".join(
        (
            str(principal.organization_id),
            str(principal.id),
            str(version_id or "00000000-0000-0000-0000-000000000000"),
            str(connector.id),
        )
    )
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    configured = configuration.get("rate_limit_per_minute")
    limit = configured if isinstance(configured, int) and not isinstance(configured, bool) else None
    try:
        allowed = await rate_limiter.allow(rate_key, limit if limit is not None else default_limit)
    except RateLimitUnavailable as exc:
        raise ObsionError(
            "rate_limit_unavailable",
            "The capability safety service is temporarily unavailable",
            status_code=503,
        ) from exc
    if not allowed:
        raise ObsionError(
            "capability_rate_limited",
            "The capability rate limit has been reached",
            status_code=429,
        )


def _positive_int(value: Any, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            "knowledge_sync_budget_invalid",
            f"knowledge_sync_budget.{field} must be a positive integer",
        )
    return int(value)


def _non_negative_int(value: Any, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(
            "knowledge_sync_budget_invalid",
            f"knowledge_sync_budget.{field} must be a non-negative integer",
        )
    return int(value)
