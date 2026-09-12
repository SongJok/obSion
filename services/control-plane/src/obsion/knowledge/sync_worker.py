"""One Python host worker: durable discovery, governed reads, fenced acceptance.

No external transport is used here except the Capability Gateway. A claimed
source is processed for one bounded page or document before yielding the lease.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.dingtalk_docs import DingTalkDocsResponseError
from obsion.capabilities.dingtalk_managed import (
    MANAGED_CONNECTOR_TYPE,
    MANAGED_DISCOVER_OPERATION,
    MANAGED_READ_OPERATION,
)
from obsion.capabilities.gateway import (
    CapabilityGateway,
    GatewayResult,
    GatewayStatus,
    OperatorGatewayRequest,
)
from obsion.common.errors import AuthorizationError, ObsionError
from obsion.common.time import utc_now
from obsion.db.models import Connector, KnowledgeSyncItem, KnowledgeSyncSource
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
)
from obsion.db.session import Database
from obsion.domain.enums import ActorType, RiskLevel
from obsion.knowledge.sync import KnowledgeSyncService, _same_connection
from obsion.knowledge.sync_queue import checkpoint_source, claim_source
from obsion.persistence.audit import AuditDraft
from obsion.security.auth import load_principal_by_id


class KnowledgeSyncWorker:
    def __init__(
        self, database: Database, gateway: CapabilityGateway, sync: KnowledgeSyncService
    ) -> None:
        self.database, self.gateway, self.sync = database, gateway, sync

    async def _locked(
        self, session: AsyncSession, claim: KnowledgeSyncSource
    ) -> KnowledgeSyncSource | None:
        source: KnowledgeSyncSource | None = await session.scalar(
            select(KnowledgeSyncSource)
            .where(
                KnowledgeSyncSource.id == claim.id,
                KnowledgeSyncSource.organization_id == claim.organization_id,
                KnowledgeSyncSource.active.is_(True),
                KnowledgeSyncSource.lease_token == claim.lease_token,
                KnowledgeSyncSource.generation == claim.generation,
                KnowledgeSyncSource.lease_expires_at > utc_now(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        return source

    async def tick(self) -> bool:
        """Return False only when no source was due; never log content or raw errors."""
        async with self.database.sessions() as session, session.begin():
            claim = await claim_source(session)
        if claim is None:
            return False
        state = deepcopy(claim.scan_state) or {
            "stage": "workspaces",
            "cursor": None,
            "seen_cursors": [],
            "queue": [],
            "workspaces": [],
            "enumeration_complete": False,
        }
        node_id: str | None = None
        try:
            async with asyncio.timeout(100):
                async with self.database.sessions() as session, session.begin():
                    source = await self._locked(session, claim)
                    if source is None:
                        return True
                    principal = await load_principal_by_id(
                        session, source.organization_id, source.user_id
                    )
                    version = await session.get(
                        ConnectorConfigurationVersion, source.connector_version_id
                    )
                    assert version is not None
                    connector = await session.get(Connector, version.connector_id)
                    if (
                        connector is None
                        or not _same_connection(connector, version)
                        or await session.get(ConnectorVersionRevocation, version.id) is not None
                    ):
                        raise AuthorizationError(
                            "knowledge_write_denied", "The source connection has changed"
                        )
                    _, installation = await self.sync._identity(
                        session, principal, connector, source.principal_binding_id
                    )
                    if (
                        installation.corp_id != source.corp_id
                        or installation.app_key != source.app_key
                    ):
                        raise AuthorizationError(
                            "knowledge_write_denied", "The source installation has changed"
                        )
                    await self.sync._authorize(
                        session,
                        principal,
                        {
                            "source": "dingtalk-managed",
                            "source_id": str(source.id),
                            "corp_id": source.corp_id,
                            "operation": "synchronize",
                        },
                        uuid4(),
                    )
                    payload: dict[str, Any]
                    if state["stage"] == "read":
                        item = await session.scalar(
                            select(KnowledgeSyncItem)
                            .where(
                                KnowledgeSyncItem.source_id == source.id,
                                KnowledgeSyncItem.seen_generation == source.generation,
                                KnowledgeSyncItem.read_generation != source.generation,
                                KnowledgeSyncItem.kind == "adoc",
                            )
                            .order_by(KnowledgeSyncItem.id)
                            .limit(1)
                        )
                        if item is None:
                            assert claim.lease_token is not None
                            await checkpoint_source(
                                session,
                                source_id=source.id,
                                lease_token=claim.lease_token,
                                generation=claim.generation,
                                scan_state={
                                    "stage": "COMPLETE",
                                    "queue": [],
                                    "cursor": None,
                                    "enumeration_complete": state["enumeration_complete"],
                                },
                                complete=True,
                            )
                            return True
                        node_id = item.node_id
                        payload = {
                            "operation": MANAGED_READ_OPERATION,
                            "workspace_id": item.workspace_id,
                            "node_id": node_id,
                        }
                    elif state["stage"] == "workspaces":
                        payload = {
                            "operation": MANAGED_DISCOVER_OPERATION,
                            "stage": "workspaces",
                            "cursor": state["cursor"],
                        }
                    elif state["stage"] == "nodes" and state["queue"]:
                        parent = state["queue"][0]
                        payload = {
                            "operation": MANAGED_DISCOVER_OPERATION,
                            "stage": "nodes",
                            "workspace_id": parent["workspace_id"],
                            "parent_id": parent["parent_id"],
                            "cursor": parent["cursor"],
                        }
                    else:
                        raise DingTalkDocsResponseError("Invalid durable scan state")
                    connector_id = connector.id
                request = OperatorGatewayRequest(
                    principal=principal,
                    capability_name=payload["operation"],
                    payload=payload,
                    resource={
                        "source": "dingtalk-managed",
                        "corp_id": claim.corp_id,
                        **{
                            key: payload[key]
                            for key in ("workspace_id", "node_id", "parent_id", "stage")
                            if key in payload
                        },
                    },
                    environment="development",
                    correlation_id=uuid4(),
                    connector_id=connector_id,
                )
                async with self.database.sessions() as session:
                    result = await self.gateway.invoke_operator(session, request)
                if result.status != GatewayStatus.COMPLETED:
                    await self._failure(
                        claim,
                        state,
                        node_id,
                        result.error_code or "dingtalk_docs_upstream_unavailable",
                        denied=(
                            result.status == GatewayStatus.DENIED
                            or result.error_code
                            in {
                                "dingtalk_docs_upstream_denied",
                                "knowledge_write_denied",
                                "capability_denied",
                            }
                        ),
                    )
                    return True
                async with self.database.sessions() as session, session.begin():
                    source = await self._locked(session, claim)
                    if source is None:
                        return True
                    if node_id is not None:
                        if (
                            not result.output
                            or result.output.get("node_id") != node_id
                            or result.output.get("workspace_id") != payload["workspace_id"]
                        ):
                            raise DingTalkDocsResponseError(
                                "Read result did not match the requested item",
                            )
                        await self.sync.accept_read(
                            session,
                            principal,
                            source_id=source.id,
                            result=result,
                            correlation_id=request.correlation_id,
                        )
                    else:
                        version = await session.get(
                            ConnectorConfigurationVersion, source.connector_version_id
                        )
                        assert version is not None
                        current_connector = await session.get(Connector, version.connector_id)
                        if (
                            current_connector is None
                            or not _same_connection(current_connector, version)
                            or await session.get(ConnectorVersionRevocation, version.id) is not None
                        ):
                            raise AuthorizationError(
                                "knowledge_write_denied", "The source connection has changed"
                            )
                        await self.sync._identity(
                            session, principal, current_connector, source.principal_binding_id
                        )
                        await self.sync._authorize(
                            session,
                            principal,
                            {
                                "source": "dingtalk-managed",
                                "source_id": str(source.id),
                                "corp_id": source.corp_id,
                                "operation": "accept-discovery",
                            },
                            request.correlation_id,
                        )
                        await self._page(session, source, state, result, connector_id, payload)
                    source.last_error_code = None
                    await session.flush()
                    state.pop("failures", None)
                    assert claim.lease_token is not None
                    if not await checkpoint_source(
                        session,
                        source_id=source.id,
                        lease_token=claim.lease_token,
                        generation=claim.generation,
                        scan_state=state,
                    ):
                        await session.rollback()
        except ObsionError as exc:
            await self._failure(claim, state, node_id, exc.code, denied=exc.status_code == 403)
        except (TimeoutError, OSError):
            await self._failure(claim, state, node_id, "dingtalk_docs_upstream_unavailable")
        return True

    async def _page(
        self,
        session: AsyncSession,
        source: KnowledgeSyncSource,
        state: dict[str, Any],
        result: GatewayResult,
        connector_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        data = result.output
        if (
            result.connector_id != connector_id
            or result.policy_decision_id is None
            or not isinstance(data, dict)
            or data.get("adapter") != MANAGED_CONNECTOR_TYPE
            or data.get("operation") != MANAGED_DISCOVER_OPERATION
            or data.get("corp_id") != source.corp_id
            or data.get("binding_id") != str(source.principal_binding_id)
            or data.get("reader_user_id") != str(source.user_id)
            or data.get("stage") != payload["stage"]
            or any(data.get(key) != payload.get(key) for key in ("workspace_id", "parent_id"))
            or not isinstance(data.get("items"), list)
        ):
            raise DingTalkDocsResponseError("Invalid governed discovery result")
        cursor = data.get("cursor")
        active = state if state["stage"] == "workspaces" else state["queue"][0]
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor.strip()
            or cursor == payload["cursor"]
            or cursor in active["seen_cursors"]
        ):
            raise DingTalkDocsResponseError("Discovery cursor did not advance")
        if cursor is not None:
            active["seen_cursors"].append(cursor)
        active["cursor"] = cursor
        for record in data["items"]:
            if state["stage"] == "workspaces":
                if record["workspace_id"] in state["workspaces"]:
                    raise DingTalkDocsResponseError("Discovery repeated a workspace")
                state["workspaces"].append(record["workspace_id"])
                state["queue"].append(
                    {
                        "workspace_id": record["workspace_id"],
                        "parent_id": record["root_node_id"],
                        "cursor": None,
                        "seen_cursors": [],
                        "depth": 0,
                    }
                )
                continue
            item = await session.scalar(
                select(KnowledgeSyncItem).where(
                    KnowledgeSyncItem.source_id == source.id,
                    KnowledgeSyncItem.node_id == record["node_id"],
                )
            )
            if item is not None and item.seen_generation == source.generation:
                raise DingTalkDocsResponseError("Discovery repeated a node or found a cycle")
            if item is None:
                item = KnowledgeSyncItem(
                    organization_id=source.organization_id,
                    source_id=source.id,
                    node_id=record["node_id"],
                    workspace_id=record["workspace_id"],
                    kind=record["kind"],
                    title=record["title"],
                )
                session.add(item)
            preserve_access = (
                item.status == "READY"
                and item.kind == record["kind"] == "adoc"
                and item.workspace_id == record["workspace_id"]
            )
            item.workspace_id, item.title, item.kind = (
                record["workspace_id"],
                record["title"],
                record["kind"],
            )
            item.seen_generation = source.generation
            # A routine directory page is neither a body/permission refresh nor
            # a revocation. Keep the old lease unchanged until the queued read
            # succeeds, fails, or the lease expires on its own.
            if not preserve_access:
                item.access_expires_at = None
                item.status = "PENDING" if item.kind == "adoc" else "PARTIAL"
            item.gaps = (
                []
                if item.kind == "adoc"
                else ["container" if item.kind == "folder" else "unsupported_format"]
            )
            item.last_error_code = None
            if record["has_children"]:
                if active["depth"] >= 64:
                    raise DingTalkDocsResponseError("Discovery exceeded its depth budget")
                state["queue"].append(
                    {
                        "workspace_id": record["workspace_id"],
                        "parent_id": record["node_id"],
                        "cursor": None,
                        "seen_cursors": [],
                        "depth": active["depth"] + 1,
                    }
                )
            # Autoflush is disabled: duplicates later in the same page must still
            # see the row and generation we just accepted.
            await session.flush()
        if cursor is None:
            if state["stage"] == "workspaces":
                state["stage"] = "nodes"
            else:
                state["queue"].pop(0)
            if not state["queue"]:
                state["stage"], state["enumeration_complete"] = "read", True

    async def _failure(
        self,
        claim: KnowledgeSyncSource,
        state: dict[str, Any],
        node_id: str | None,
        code: str,
        *,
        denied: bool = False,
    ) -> None:
        # Keep the last committed cursor, not the in-memory partially processed page.
        async with self.database.sessions() as session, session.begin():
            source = await self._locked(session, claim)
            if source is None:
                return
            persisted = deepcopy(claim.scan_state) or {
                **state,
                "cursor": None,
                "queue": [],
                "workspaces": [],
                "seen_cursors": [],
                "stage": "workspaces",
                "enumeration_complete": False,
            }
            attempts = min(int(persisted.get("failures", 0)) + 1, 8)
            # A failed document must not exponentially delay independent next
            # documents. Directory failures retry the same cursor with backoff.
            if node_id is not None:
                attempts = 1
            persisted["failures"] = attempts
            if code in {"dingtalk_docs_upstream_denied", "capability_denied"}:
                safe_code = "dingtalk_docs_upstream_denied"
            elif code in {"dingtalk_docs_response_invalid", "capability_output_invalid"}:
                safe_code = "dingtalk_docs_response_invalid"
            elif code == "capability_rate_limited":
                safe_code = "capability_rate_limited"
            elif code == "credential_unavailable":
                safe_code = "credential_unavailable"
            elif code == "knowledge_write_denied":
                safe_code = "knowledge_write_denied"
            elif code == "document_parse_failed":
                safe_code = "document_parse_failed"
            elif code == "artifact_store_unavailable":
                safe_code = "artifact_store_unavailable"
            elif code == "resource_not_found":
                safe_code = "resource_not_found"
            else:
                safe_code = "dingtalk_docs_upstream_unavailable"
            if node_id is not None:
                item = await session.scalar(
                    select(KnowledgeSyncItem)
                    .where(
                        KnowledgeSyncItem.source_id == source.id,
                        KnowledgeSyncItem.node_id == node_id,
                    )
                    .with_for_update()
                )
                if item is not None:
                    item.read_generation = source.generation
                    item.status = "DENIED" if denied else "FAILED"
                    item.access_expires_at = None
                    item.last_error_code = safe_code
            else:
                await session.execute(
                    update(KnowledgeSyncItem)
                    .where(
                        KnowledgeSyncItem.source_id == source.id,
                    )
                    .values(
                        access_expires_at=None,
                        status=case(
                            (KnowledgeSyncItem.status == "READY", "DENIED" if denied else "FAILED"),
                            else_=KnowledgeSyncItem.status,
                        ),
                    )
                )
            assert claim.lease_token is not None
            if await checkpoint_source(
                session,
                source_id=source.id,
                lease_token=claim.lease_token,
                generation=claim.generation,
                scan_state=persisted,
            ):
                source.next_poll_at = utc_now() + timedelta(seconds=min(300, 2**attempts))
                source.last_error_code = safe_code
                await self.sync.audit.write(
                    session,
                    AuditDraft(
                        organization_id=source.organization_id,
                        correlation_id=uuid4(),
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        action="knowledge.write",
                        resource_type="knowledge_source",
                        resource_id=str(source.id),
                        outcome="DENIED" if denied else "FAILED",
                        risk_level=RiskLevel.L2,
                        metadata={
                            "error_code": safe_code,
                            "generation": claim.generation,
                            "stage": persisted["stage"],
                        },
                    ),
                )
            else:
                # The lease can expire after the initial lock. Roll back both
                # ORM item changes and bulk access invalidation on that boundary.
                await session.rollback()
