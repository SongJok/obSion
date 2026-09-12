"""Transactional source registration and acceptance of governed reader results.

Only the trusted control-plane sync worker supplies Gateway results here. Public
clients must not be given an endpoint accepting these result objects or bodies.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.dingtalk_docs import DingTalkDocsResponseError
from obsion.capabilities.dingtalk_managed import MANAGED_CONNECTOR_TYPE, MANAGED_READ_OPERATION
from obsion.capabilities.gateway import GatewayResult, GatewayStatus
from obsion.common.errors import AuthorizationError, NotFoundError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import (
    Connector,
    ImInstallation,
    ImPrincipalBinding,
    KnowledgeSyncItem,
    KnowledgeSyncSource,
)
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
)
from obsion.domain.enums import (
    ActorType,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    RiskLevel,
)
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.source_access import MANAGED_KNOWLEDGE_SOURCE
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput

ACCESS_LEASE_SECONDS = 300


def _same_connection(connector: Connector, version: ConnectorConfigurationVersion) -> bool:
    return all(
        getattr(connector, field) == getattr(version, field)
        for field in (
            "connector_type",
            "environment",
            "endpoint",
            "configuration",
            "credential_ref",
            "declared_grants",
            "allowed_egress",
        )
    )


class KnowledgeSyncService:
    def __init__(self, knowledge: KnowledgeService) -> None:
        self.knowledge = knowledge
        self.policy = PolicyEngine()
        self.audit = AuditWriter()

    async def _authorize(
        self,
        session: AsyncSession,
        principal: Principal,
        resource: dict[str, Any],
        correlation_id: UUID,
    ) -> UUID:
        current = await load_principal_by_id(session, principal.organization_id, principal.id)
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=current,
                action="knowledge.write",
                resource=resource,
                context={"entrypoint": "knowledge-sync"},
                risk_level=RiskLevel.L2,
                resource_type="knowledge_source",
            ),
        )
        allowed = current.can("knowledge.write") and decision.effect == DecisionEffect.ALLOW
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="knowledge.write",
                resource_type="knowledge_source",
                outcome="AUTHORIZED" if allowed else "DENIED",
                policy_decision_id=decision.id,
                risk_level=RiskLevel.L2,
                resource=resource,
            ),
        )
        if not allowed:
            raise AuthorizationError(
                "knowledge_write_denied", "Source synchronization is not permitted"
            )
        return decision.id

    async def _identity(
        self,
        session: AsyncSession,
        principal: Principal,
        connector: Connector,
        binding_id: UUID,
    ) -> tuple[ImPrincipalBinding, ImInstallation]:
        row = (
            await session.execute(
                select(ImPrincipalBinding, ImInstallation)
                .join(ImInstallation, ImInstallation.id == ImPrincipalBinding.installation_id)
                .where(
                    ImPrincipalBinding.id == binding_id,
                    ImPrincipalBinding.organization_id == principal.organization_id,
                    ImInstallation.organization_id == principal.organization_id,
                    ImPrincipalBinding.user_id == principal.id,
                    ImPrincipalBinding.active.is_(True),
                    ImPrincipalBinding.revoked_at.is_(None),
                    ImInstallation.active.is_(True),
                    ImInstallation.revoked_at.is_(None),
                    ImPrincipalBinding.channel == "dingtalk",
                    ImInstallation.channel == "dingtalk",
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        config = connector.configuration
        if (
            row is None
            or not isinstance(config, dict)
            or any(
                not isinstance(config.get(key), str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", config[key]) is None
                for key in ("corp_id", "user_id", "operator_id", "installation_id")
            )
            or not isinstance(row[1].app_key, str)
            or not row[1].app_key.strip()
            or connector.status != ConnectorStatus.ACTIVE
            or connector.connector_type != MANAGED_CONNECTOR_TYPE
            or connector.environment != "development"
            or connector.endpoint
            or connector.allowed_egress
            or row[0].sender_id != config.get("user_id")
            or row[1].corp_id != config.get("corp_id")
            or str(row[1].id) != config.get("installation_id")
        ):
            raise AuthorizationError("knowledge_write_denied", "The source identity is not active")
        return row[0], row[1]

    async def create_source(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        connector_id: UUID,
        binding_id: UUID,
        correlation_id: UUID,
    ) -> KnowledgeSyncSource:
        connector = await session.scalar(
            select(Connector)
            .where(
                Connector.id == connector_id,
                Connector.organization_id == principal.organization_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if connector is None:
            raise NotFoundError("Connector", connector_id)
        binding, installation = await self._identity(session, principal, connector, binding_id)
        await self._authorize(
            session,
            principal,
            {
                "source": MANAGED_KNOWLEDGE_SOURCE,
                "connector_id": str(connector.id),
                "corp_id": installation.corp_id,
                "operation": "register",
            },
            correlation_id,
        )
        candidates = (
            await session.scalars(
                select(ConnectorConfigurationVersion)
                .where(
                    ConnectorConfigurationVersion.organization_id == principal.organization_id,
                    ConnectorConfigurationVersion.connector_id == connector.id,
                )
                .order_by(ConnectorConfigurationVersion.created_at.desc())
            )
        ).all()
        version = None
        for candidate in candidates:
            if (
                _same_connection(connector, candidate)
                and await session.get(ConnectorVersionRevocation, candidate.id) is None
            ):
                version = candidate
                break
        if version is None:
            version = ConnectorConfigurationVersion(
                organization_id=principal.organization_id,
                connector_id=connector.id,
                connector_type=connector.connector_type,
                environment=connector.environment,
                endpoint=connector.endpoint,
                configuration=dict(connector.configuration),
                credential_ref=connector.credential_ref,
                declared_grants=list(connector.declared_grants),
                allowed_egress=list(connector.allowed_egress),
                created_by=principal.id,
            )
            session.add(version)
            await session.flush()
        source = await session.scalar(
            select(KnowledgeSyncSource).where(
                KnowledgeSyncSource.organization_id == principal.organization_id,
                KnowledgeSyncSource.connector_version_id == version.id,
                KnowledgeSyncSource.principal_binding_id == binding.id,
            )
        )
        if source is None:
            source = KnowledgeSyncSource(
                organization_id=principal.organization_id,
                connector_version_id=version.id,
                principal_binding_id=binding.id,
                user_id=principal.id,
                corp_id=installation.corp_id,
                app_key=installation.app_key,
                active=True,
                generation=0,
                scan_state={},
                next_poll_at=utc_now(),
            )
            session.add(source)
            await session.flush()
        # Idempotent registration does not reactivate an explicitly disabled source.
        return source

    async def accept_read(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        source_id: UUID,
        result: GatewayResult,
        correlation_id: UUID,
    ) -> KnowledgeSyncItem:
        source = await session.scalar(
            select(KnowledgeSyncSource)
            .where(
                KnowledgeSyncSource.id == source_id,
                KnowledgeSyncSource.organization_id == principal.organization_id,
                KnowledgeSyncSource.user_id == principal.id,
                KnowledgeSyncSource.active.is_(True),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if source is None:
            raise NotFoundError("Knowledge source", source_id)
        version = await session.get(ConnectorConfigurationVersion, source.connector_version_id)
        assert version is not None
        connector = await session.get(Connector, version.connector_id, populate_existing=True)
        if (
            connector is None
            or not _same_connection(connector, version)
            or await session.get(ConnectorVersionRevocation, version.id) is not None
        ):
            raise AuthorizationError("knowledge_write_denied", "The source connection has changed")
        _, installation = await self._identity(
            session, principal, connector, source.principal_binding_id
        )
        if installation.corp_id != source.corp_id or installation.app_key != source.app_key:
            raise AuthorizationError(
                "knowledge_write_denied", "The source installation has changed"
            )
        data = result.output
        if (
            result.status != GatewayStatus.COMPLETED
            or result.connector_id != connector.id
            or result.policy_decision_id is None
            or not isinstance(data, dict)
            or data.get("operation") != MANAGED_READ_OPERATION
            or data.get("adapter") != MANAGED_CONNECTOR_TYPE
            or data.get("corp_id") != source.corp_id
            or data.get("reader_user_id") != str(principal.id)
            or data.get("binding_id") != str(source.principal_binding_id)
            or not isinstance(data.get("complete"), bool)
            or not isinstance(data.get("gaps"), list)
            or any(not isinstance(gap, str) for gap in data.get("gaps", []))
        ):
            raise DingTalkDocsResponseError("The governed reader result is invalid")
        for key in (
            "node_id",
            "workspace_id",
            "title",
            "revision",
            "parser_version",
            "raw_checksum_sha256",
            "observed_at",
        ):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise DingTalkDocsResponseError("The reader omitted source provenance")
        try:
            observed_at = datetime.fromisoformat(data["observed_at"])
        except ValueError as exc:
            raise DingTalkDocsResponseError("The reader timestamp is invalid") from exc
        now = utc_now()
        if (
            observed_at.tzinfo is None
            or not now - timedelta(seconds=ACCESS_LEASE_SECONDS) < observed_at <= now
        ):
            raise DingTalkDocsResponseError("The reader authorization is stale")
        decision_id = await self._authorize(
            session,
            principal,
            {
                "source": MANAGED_KNOWLEDGE_SOURCE,
                "source_id": str(source.id),
                "corp_id": source.corp_id,
                "node_id": data["node_id"],
                "operation": "accept",
            },
            correlation_id,
        )
        item = await session.scalar(
            select(KnowledgeSyncItem)
            .where(
                KnowledgeSyncItem.source_id == source.id,
                KnowledgeSyncItem.node_id == data["node_id"],
            )
            .with_for_update()
        )
        if item is None:
            item = KnowledgeSyncItem(
                organization_id=principal.organization_id,
                source_id=source.id,
                node_id=data["node_id"],
                workspace_id=data["workspace_id"],
                kind="adoc",
                title=data["title"],
            )
            session.add(item)
        item.title, item.workspace_id = data["title"], data["workspace_id"]
        item.seen_generation = source.generation
        item.read_generation = source.generation
        item.source_revision, item.checked_at = data["revision"], observed_at
        item.gaps = data.get("gaps", ["unknown_completeness"])
        item.status = "PENDING"
        item.access_expires_at = None
        if data.get("complete") is not True or item.gaps:
            item.status = "PARTIAL"
        else:
            body = data.get("text")
            if not isinstance(body, str) or not body.strip():
                raise ValidationError("document_parse_failed", "The source has no usable text")
            document, _, _ = await self.knowledge.ingest(
                session,
                principal,
                source=MANAGED_KNOWLEDGE_SOURCE,
                external_id=f"{source.id}:{item.node_id}",
                title=item.title,
                media_type="text/plain",
                filename=f"{item.node_id}.txt",
                content=body.encode(),
                classification=Classification.RESTRICTED,
                acl={"organization": False, "users": [str(principal.id)]},
                extra_metadata={
                    "revision_id": item.source_revision,
                    "source_id": str(source.id),
                    "corp_id": source.corp_id,
                    "external_id": item.node_id,
                    "source_parser": data["parser_version"],
                    "raw_checksum_sha256": data["raw_checksum_sha256"],
                    "reader_policy_decision_id": str(result.policy_decision_id),
                    "connector_name": connector.name,
                    "connector_id": str(connector.id),
                    "operation": MANAGED_READ_OPERATION,
                },
            )
            item.document_id, item.status = document.id, "READY"
            item.access_expires_at = observed_at + timedelta(seconds=ACCESS_LEASE_SECONDS)
        await session.flush()
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="knowledge.write",
                resource_type="knowledge_source",
                resource_id=str(source.id),
                outcome=item.status,
                policy_decision_id=decision_id,
                risk_level=RiskLevel.L2,
                metadata={
                    "item_id": str(item.id),
                    "document_id": str(item.document_id) if item.document_id else None,
                    "reader_policy_decision_id": str(result.policy_decision_id),
                },
            ),
        )
        return item

    async def disable_source(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        source_id: UUID,
        correlation_id: UUID,
    ) -> None:
        source = await session.scalar(
            select(KnowledgeSyncSource)
            .where(
                KnowledgeSyncSource.id == source_id,
                KnowledgeSyncSource.organization_id == principal.organization_id,
                KnowledgeSyncSource.user_id == principal.id,
            )
            .with_for_update()
        )
        if source is None:
            raise NotFoundError("Knowledge source", source_id)
        decision_id = await self._authorize(
            session,
            principal,
            {
                "source": MANAGED_KNOWLEDGE_SOURCE,
                "source_id": str(source.id),
                "corp_id": source.corp_id,
                "operation": "disable",
            },
            correlation_id,
        )
        source.active = False
        source.lease_token = None
        source.lease_expires_at = None
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="knowledge.write",
                resource_type="knowledge_source",
                resource_id=str(source.id),
                outcome="DISABLED",
                policy_decision_id=decision_id,
                risk_level=RiskLevel.L2,
            ),
        )

    async def schedule_source(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        source_id: UUID,
        correlation_id: UUID,
        resume: bool = False,
    ) -> KnowledgeSyncSource:
        """Resume/restart from discovery without renewing any document grant."""
        source = await session.scalar(
            select(KnowledgeSyncSource)
            .where(
                KnowledgeSyncSource.id == source_id,
                KnowledgeSyncSource.organization_id == principal.organization_id,
                KnowledgeSyncSource.user_id == principal.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if source is None:
            raise NotFoundError("Knowledge source", source_id)
        decision_id = await self._authorize(
            session,
            principal,
            {
                "source": MANAGED_KNOWLEDGE_SOURCE,
                "source_id": str(source.id),
                "corp_id": source.corp_id,
                "operation": "resume" if resume else "synchronize",
            },
            correlation_id,
        )
        version = await session.get(ConnectorConfigurationVersion, source.connector_version_id)
        assert version is not None
        connector = await session.get(Connector, version.connector_id, populate_existing=True)
        if (
            connector is None
            or not _same_connection(connector, version)
            or await session.get(ConnectorVersionRevocation, version.id) is not None
        ):
            raise AuthorizationError("knowledge_write_denied", "The source connection has changed")
        _, installation = await self._identity(
            session, principal, connector, source.principal_binding_id
        )
        if installation.corp_id != source.corp_id or installation.app_key != source.app_key:
            raise AuthorizationError(
                "knowledge_write_denied", "The source installation has changed"
            )
        if not source.active and not resume:
            raise ValidationError(
                "dingtalk_docs_operation_invalid", "Resume the paused source first"
            )
        if not source.active:
            # Resuming a stopped source cannot restore an old READY lease. The
            # source must obtain new upstream checks before returning documents.
            from sqlalchemy import update

            await session.execute(
                update(KnowledgeSyncItem)
                .where(
                    KnowledgeSyncItem.source_id == source.id,
                    KnowledgeSyncItem.status == "READY",
                )
                .values(status="PENDING", access_expires_at=None)
            )
        source.active = True
        source.scan_state = {}
        source.lease_token = None
        source.lease_expires_at = None
        source.next_poll_at = utc_now()
        source.last_error_code = None
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="knowledge.write",
                resource_type="knowledge_source",
                resource_id=str(source.id),
                outcome="SCHEDULED",
                policy_decision_id=decision_id,
                risk_level=RiskLevel.L2,
            ),
        )
        return source
