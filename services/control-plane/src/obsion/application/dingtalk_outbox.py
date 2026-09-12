"""Durable one-to-one DingTalk robot delivery.

The worker that uses this service commits a claim before vendor I/O. This
module intentionally does not expose an Agent tool or an HTTP send endpoint.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.dingtalk_robot import (
    DingTalkRobotTransport,
    RobotQueryState,
    RobotSendState,
    connector_fingerprint,
)
from obsion.capabilities.gateway import (
    CapabilityGateway,
    DingTalkRobotOutboxQueryRequest,
    DingTalkRobotOutboxRequest,
)
from obsion.common.errors import AuthorizationError, NotFoundError, ObsionError
from obsion.common.time import ensure_utc, utc_now
from obsion.db.im_models import (
    ImGroupAudience,
    ImInboxMessage,
    ImInstallationBinding,
)
from obsion.db.models import (
    Artifact,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    DingTalkRobotOutbox,
    ImInstallation,
    Run,
    Thread,
    Turn,
    Workspace,
    WorkspaceMember,
)
from obsion.domain.enums import (
    ActorType,
    ArtifactKind,
    ConnectorStatus,
    DingTalkOutboxStatus,
    RegistryStatus,
    RiskLevel,
    RunStatus,
    SideEffect,
)
from obsion.knowledge.publication import KnowledgePublicationGuard
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.source_fence import acquire_source_publication_fence
from obsion.security.workspace_access import require_run_access

_CAPABILITY: Final = "im.dingtalk.robot.reply"
_PROTOCOL: Final = "dingtalk.robot.oto.v1"
_FALLBACK_FINGERPRINT: Final = hashlib.sha256(b"").hexdigest()
_ENV_NAME: Final = re.compile(r"^OBSION_[A-Z][A-Z0-9_]*$")
_SAFE_GROUP_STATUS: Final = "任务已完成，结果未在群内公开。请在 Obsion 工作台登录查看。"
_CLASSIFICATION_RANK: Final = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "RESTRICTED": 3,
}


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    outbox_id: UUID
    fencing_token: int


@dataclass(frozen=True, slots=True)
class OutboxReconciliationClaim:
    outbox_id: UUID
    attempt_count: int


class DingTalkOutboxService:
    def __init__(
        self,
        gateway: CapabilityGateway,
        *,
        lease_seconds: int,
        max_attempts: int,
        transport: DingTalkRobotTransport | None = None,
    ) -> None:
        self.gateway = gateway
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.max_reconciliation_attempts = max(1, max_attempts)
        self.transport = transport
        self.audit = AuditWriter()

    async def list_for_admin(
        self, session: AsyncSession, principal: Principal, *, limit: int = 100
    ) -> list[DingTalkRobotOutbox]:
        if not principal.can("admin.read"):
            raise AuthorizationError(
                "admin_access_denied", "Administration access is not permitted"
            )
        return list(
            await session.scalars(
                select(DingTalkRobotOutbox)
                .where(DingTalkRobotOutbox.organization_id == principal.organization_id)
                .order_by(DingTalkRobotOutbox.updated_at.desc())
                .limit(min(max(limit, 1), 100))
            )
        )

    async def enqueue_next(self, session: AsyncSession) -> DingTalkRobotOutbox | None:
        """Materialize one completed direct-message Run, or refresh a blocked row.

        The Inbox row is locked before testing its mutable authorization inputs;
        this prevents two workers from creating two logical deliveries.
        """

        row = await session.execute(
            select(
                ImInboxMessage,
                Run,
                ImInstallation,
                ImInstallationBinding,
                Connector,
                DingTalkRobotOutbox,
                ImGroupAudience,
            )
            .join(Run, Run.id == ImInboxMessage.run_id)
            .join(ImInstallation, ImInstallation.id == ImInboxMessage.installation_id)
            .join(ImInstallationBinding, ImInstallationBinding.id == ImInboxMessage.binding_id)
            .join(Connector, Connector.id == ImInstallation.connector_id)
            .outerjoin(DingTalkRobotOutbox, DingTalkRobotOutbox.run_id == Run.id)
            .outerjoin(
                ImGroupAudience,
                and_(
                    ImGroupAudience.organization_id == ImInboxMessage.organization_id,
                    ImGroupAudience.installation_id == ImInboxMessage.installation_id,
                    ImGroupAudience.conversation_id == ImInboxMessage.conversation_id,
                    ImGroupAudience.status == "ACTIVE",
                ),
            )
            .where(
                ImInstallation.provider == "dingtalk",
                Run.status == RunStatus.COMPLETED,
                or_(
                    ImInboxMessage.conversation_type == "direct",
                    and_(
                        ImInboxMessage.conversation_type == "group",
                        ImGroupAudience.id.is_not(None),
                    ),
                ),
                (DingTalkRobotOutbox.id.is_(None))
                | (
                    (DingTalkRobotOutbox.status == DingTalkOutboxStatus.BLOCKED)
                    & (DingTalkRobotOutbox.blocked_reason != "delivery_retry_exhausted")
                ),
            )
            .order_by(Run.completed_at, ImInboxMessage.created_at)
            .with_for_update(of=ImInboxMessage, skip_locked=True)
            .limit(1)
        )
        candidate = row.one_or_none()
        if candidate is None:
            return None
        inbox, run, installation, binding, connector, raw_outbox, audience = candidate._tuple()
        existing_outbox = cast(DingTalkRobotOutbox | None, raw_outbox)
        outbox_item: DingTalkRobotOutbox
        if existing_outbox is None:
            created_outbox = DingTalkRobotOutbox(
                organization_id=inbox.organization_id,
                installation_id=installation.id,
                inbox_message_id=inbox.id,
                binding_id=binding.id,
                run_id=run.id,
                recipient_user_id=inbox.subject_user_id,
                recipient_sender_id=inbox.sender_id,
                conversation_type=inbox.conversation_type,
                recipient_conversation_id=(
                    inbox.conversation_id if inbox.conversation_type == "group" else None
                ),
                audience_id=audience.id if audience is not None else None,
                audience_member_fingerprint=(
                    audience.member_fingerprint if audience is not None else None
                ),
                connector_id=connector.id,
                capability_version_id=None,
                connector_fingerprint=_fingerprint(connector),
                content_fingerprint=_FALLBACK_FINGERPRINT,
                status=DingTalkOutboxStatus.BLOCKED,
                attempt_count=0,
                fencing_token=0,
            )
            session.add(created_outbox)
            try:
                await session.flush()
            except IntegrityError:
                # A concurrent worker committed first. Its durable state is authoritative.
                return None
            outbox_item = created_outbox
        else:
            outbox_item = existing_outbox
        await self._prepare_queue(
            session, outbox_item, inbox, run, installation, binding, connector
        )
        await session.flush()
        return outbox_item

    async def expire_claims(self, session: AsyncSession) -> int:
        """Turn expired external-I/O claims into UNKNOWN without resending them."""

        now = utc_now()
        rows: list[DingTalkRobotOutbox] = list(
            await session.scalars(
                select(DingTalkRobotOutbox)
                .where(
                    DingTalkRobotOutbox.status == DingTalkOutboxStatus.DISPATCHING,
                    DingTalkRobotOutbox.lease_expires_at.is_not(None),
                    DingTalkRobotOutbox.lease_expires_at < now,
                )
                .order_by(DingTalkRobotOutbox.lease_expires_at)
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        )
        for outbox in rows:
            outbox.status = DingTalkOutboxStatus.UNKNOWN
            outbox.lease_expires_at = None
            await self._set_last_error(session, outbox, "delivery_lease_expired")
            await self._audit(session, outbox, "UNKNOWN")
        return len(rows)

    async def claim_reconciliation(
        self,
        session: AsyncSession,
        *,
        outbox_id: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> OutboxReconciliationClaim | None:
        """Claim one accepted vendor key before a read-only status request."""

        claim_before = utc_now() - timedelta(seconds=self.lease_seconds)
        statement = select(DingTalkRobotOutbox).where(
            DingTalkRobotOutbox.status == DingTalkOutboxStatus.ACCEPTED,
            DingTalkRobotOutbox.accepted_process_query_key.is_not(None),
            DingTalkRobotOutbox.reconciliation_attempt_count < self.max_reconciliation_attempts,
            or_(
                DingTalkRobotOutbox.last_reconciled_at.is_(None),
                DingTalkRobotOutbox.last_reconciled_at <= claim_before,
            ),
        )
        if outbox_id is not None:
            statement = statement.where(DingTalkRobotOutbox.id == outbox_id)
        if organization_id is not None:
            statement = statement.where(DingTalkRobotOutbox.organization_id == organization_id)
        outbox = await session.scalar(
            statement.order_by(
                DingTalkRobotOutbox.last_reconciled_at,
                DingTalkRobotOutbox.created_at,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if outbox is None:
            return None
        outbox.reconciliation_attempt_count += 1
        outbox.last_reconciled_at = utc_now()
        await session.flush()
        return OutboxReconciliationClaim(outbox.id, outbox.reconciliation_attempt_count)

    async def reconcile(
        self, session: AsyncSession, claim: OutboxReconciliationClaim
    ) -> DingTalkRobotOutbox | None:
        """Reconcile a previously claimed key without ever sending a message."""

        outbox = await session.scalar(
            select(DingTalkRobotOutbox)
            .where(DingTalkRobotOutbox.id == claim.outbox_id)
            .with_for_update()
        )
        if (
            outbox is None
            or outbox.status != DingTalkOutboxStatus.ACCEPTED
            or outbox.reconciliation_attempt_count != claim.attempt_count
            or not outbox.accepted_process_query_key
        ):
            return None
        inputs = await self._reconciliation_inputs(session, outbox)
        if inputs is None:
            await self._set_last_error(session, outbox, "delivery_preconditions_changed")
            await self._audit(session, outbox, "RECONCILIATION_BLOCKED")
            return outbox
        installation, connector, capability_version_id, principal = inputs
        result = await self.gateway.reconcile_dingtalk_robot_outbox(
            session,
            DingTalkRobotOutboxQueryRequest(
                principal=principal,
                run_id=outbox.run_id,
                outbox_id=outbox.id,
                installation_id=installation.id,
                recipient_sender_id=outbox.recipient_sender_id,
                app_key=_app_key(connector, installation),
                robot_code=_robot_code(connector, installation),
                process_query_key=outbox.accepted_process_query_key,
                connector_id=connector.id,
                connector_fingerprint=outbox.connector_fingerprint,
                environment=connector.environment,
                capability_version_id=capability_version_id,
                conversation_type=outbox.conversation_type,
                recipient_conversation_id=outbox.recipient_conversation_id,
            ),
            transport=self.transport,
        )
        if result.policy_decision_id is not None:
            outbox.policy_decision_id = result.policy_decision_id
        query = result.query
        outbox.vendor_send_status = query.send_status
        outbox.vendor_read_status = query.read_status
        outbox.vendor_read_at = _millis_to_datetime(query.read_timestamp_ms)
        if query.state == RobotQueryState.SUCCESS:
            await self._set_last_error(session, outbox, None)
            outcome = "RECONCILED"
        elif query.state == RobotQueryState.FAILURE:
            outbox.status = DingTalkOutboxStatus.REJECTED
            await self._set_last_error(session, outbox, "delivery_gateway_unavailable")
            outcome = "RECONCILED_FAILURE"
        else:
            reconciliation_reason = query.reason or "reconciliation_status_unknown"
            await self._set_last_error(session, outbox, reconciliation_reason)
            outcome = "RECONCILIATION_UNKNOWN"
        await self._audit(
            session,
            outbox,
            outcome,
            metadata={
                "operation": "reconcile",
                "vendor_send_status": query.send_status,
                "vendor_read_status": query.read_status,
                "reconciliation_reason": query.reason or "reconciliation_status_unknown",
            },
        )
        return outbox

    async def _reconciliation_inputs(
        self,
        session: AsyncSession,
        outbox: DingTalkRobotOutbox,
    ) -> tuple[ImInstallation, Connector, UUID, Principal] | None:
        row = await session.execute(
            select(ImInboxMessage, Run, ImInstallation, ImInstallationBinding, Connector)
            .join(Run, Run.id == ImInboxMessage.run_id)
            .join(ImInstallation, ImInstallation.id == ImInboxMessage.installation_id)
            .join(ImInstallationBinding, ImInstallationBinding.id == ImInboxMessage.binding_id)
            .join(Connector, Connector.id == ImInstallation.connector_id)
            .where(
                ImInboxMessage.id == outbox.inbox_message_id,
                ImInboxMessage.organization_id == outbox.organization_id,
                Run.id == outbox.run_id,
                Run.status == RunStatus.COMPLETED,
                ImInstallation.id == outbox.installation_id,
                ImInstallationBinding.id == outbox.binding_id,
                Connector.id == outbox.connector_id,
            )
            .execution_options(populate_existing=True)
        )
        loaded = row.one_or_none()
        if loaded is None or outbox.capability_version_id is None:
            return None
        inbox, _run, installation, binding, connector = loaded._tuple()
        if (
            not _queue_is_ready(
                inbox,
                installation,
                binding,
                connector,
                outbox.capability_version_id,
            )
            or _fingerprint(connector) != outbox.connector_fingerprint
        ):
            return None
        try:
            principal = await load_principal_by_id(
                session, outbox.organization_id, outbox.recipient_user_id
            )
            await require_run_access(session, principal, outbox.run_id)
            if inbox.conversation_type == "group":
                audience = await _load_group_audience_for_delivery(
                    session, outbox, inbox, _run, principal
                )
                if audience is None:
                    return None
        except (AuthorizationError, NotFoundError, ValueError):
            return None
        return installation, connector, outbox.capability_version_id, principal

    async def claim_next(self, session: AsyncSession) -> OutboxClaim | None:
        now = utc_now()
        outbox = await session.scalar(
            select(DingTalkRobotOutbox)
            .where(
                DingTalkRobotOutbox.status == DingTalkOutboxStatus.QUEUED,
                DingTalkRobotOutbox.next_attempt_at.is_not(None),
                DingTalkRobotOutbox.next_attempt_at <= now,
            )
            .order_by(DingTalkRobotOutbox.next_attempt_at, DingTalkRobotOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if outbox is None:
            return None
        outbox.status = DingTalkOutboxStatus.DISPATCHING
        outbox.fencing_token += 1
        outbox.attempt_count += 1
        outbox.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        outbox.next_attempt_at = None
        await self._set_last_error(session, outbox, None)
        await self._audit(session, outbox, "DISPATCHING")
        return OutboxClaim(outbox.id, outbox.fencing_token)

    async def dispatch(
        self, session: AsyncSession, claim: OutboxClaim
    ) -> DingTalkRobotOutbox | None:
        """Revalidate a claimed item, then send through the Capability Gateway."""

        outbox = await session.scalar(
            select(DingTalkRobotOutbox)
            .where(DingTalkRobotOutbox.id == claim.outbox_id)
            .with_for_update()
        )
        if (
            outbox is None
            or outbox.status != DingTalkOutboxStatus.DISPATCHING
            or outbox.fencing_token != claim.fencing_token
        ):
            return None
        try:
            await acquire_source_publication_fence(session, outbox.organization_id)
        except ObsionError as error:
            if error.code != "authorization_fence_unavailable":
                raise
            # No Gateway call has occurred: this is known not to have sent.
            await self._block(session, outbox, "authorization_fence_unavailable")
            return outbox
        inputs = await self._dispatch_inputs(session, outbox)
        if inputs is None:
            await self._block(session, outbox, "delivery_preconditions_changed")
            return outbox
        (
            inbox,
            run,
            installation,
            binding,
            connector,
            text,
            capability_version_id,
            principal,
        ) = inputs

        async def authorize_send() -> bool:
            # The native transport calls this after token acquisition, before
            # attempting the one vendor message POST. Keep the same DB fence.
            if outbox.lease_expires_at is None or ensure_utc(outbox.lease_expires_at) <= utc_now():
                return False
            current = await self._dispatch_inputs(session, outbox)
            return current is not None and current[5] == text

        result = await self.gateway.invoke_dingtalk_robot_outbox(
            session,
            DingTalkRobotOutboxRequest(
                principal=principal,
                run_id=run.id,
                outbox_id=outbox.id,
                installation_id=installation.id,
                recipient_sender_id=inbox.sender_id,
                app_key=_app_key(connector, installation),
                robot_code=_robot_code(connector, installation),
                text=text,
                connector_id=connector.id,
                connector_fingerprint=outbox.connector_fingerprint,
                environment=connector.environment,
                capability_version_id=capability_version_id,
                conversation_type=outbox.conversation_type,
                recipient_conversation_id=outbox.recipient_conversation_id,
            ),
            authorize_send=authorize_send,
            transport=self.transport,
        )
        outbox.policy_decision_id = result.policy_decision_id
        state = result.send.state
        if state == RobotSendState.ACCEPTED:
            outbox.status = DingTalkOutboxStatus.ACCEPTED
            outbox.accepted_process_query_key = result.send.process_query_key
            outbox.lease_expires_at = None
            await self._set_last_error(session, outbox, None)
        elif state == RobotSendState.REJECTED:
            outbox.status = DingTalkOutboxStatus.REJECTED
            outbox.lease_expires_at = None
            await self._set_last_error(session, outbox, result.send.reason)
        elif state == RobotSendState.UNKNOWN:
            outbox.status = DingTalkOutboxStatus.UNKNOWN
            outbox.lease_expires_at = None
            await self._set_last_error(session, outbox, result.send.reason)
        elif outbox.attempt_count >= self.max_attempts:
            await self._block(session, outbox, "delivery_retry_exhausted", result.send.reason)
            return outbox
        else:
            outbox.status = DingTalkOutboxStatus.QUEUED
            outbox.lease_expires_at = None
            outbox.next_attempt_at = utc_now() + timedelta(
                seconds=_retry_delay(outbox.attempt_count)
            )
            await self._set_last_error(session, outbox, result.send.reason)
        await self._audit(session, outbox, outbox.status.value)
        return outbox

    async def _prepare_queue(
        self,
        session: AsyncSession,
        outbox: DingTalkRobotOutbox,
        inbox: ImInboxMessage,
        run: Run,
        installation: ImInstallation,
        binding: ImInstallationBinding,
        connector: Connector,
    ) -> None:
        try:
            text, classification = await _answer_for_run_metadata(
                session, run.organization_id, run.id
            )
            principal = await load_principal_by_id(
                session, run.organization_id, inbox.subject_user_id
            )
            await require_run_access(
                session,
                principal,
                run.id,
                source_content=True,
                source_corp_id=installation.external_corp_id or "",
            )
        except (AuthorizationError, NotFoundError, ValueError):
            await self._block(session, outbox, "delivery_answer_or_access_missing")
            return
        delivery_mode = "FINAL"
        audience: ImGroupAudience | None = None
        if inbox.conversation_type == "group":
            group_delivery = await _group_delivery(
                session,
                run,
                inbox,
                principal,
                text,
                classification,
            )
            if group_delivery is None:
                await self._block(session, outbox, "group_audience_denied")
                return
            audience, delivery_mode, text = group_delivery
        capability_version_id = await _bound_capability_version(
            session, run.organization_id, connector.id, connector.environment
        )
        if not _queue_is_ready(
            inbox,
            installation,
            binding,
            connector,
            capability_version_id,
        ):
            await self._block(session, outbox, "delivery_configuration_missing")
            return
        outbox.binding_id = binding.id
        outbox.recipient_user_id = inbox.subject_user_id
        outbox.recipient_sender_id = inbox.sender_id
        outbox.conversation_type = inbox.conversation_type
        outbox.recipient_conversation_id = (
            inbox.conversation_id if inbox.conversation_type == "group" else None
        )
        outbox.audience_id = audience.id if audience is not None else None
        outbox.audience_member_fingerprint = (
            audience.member_fingerprint if audience is not None else None
        )
        outbox.delivery_mode = delivery_mode
        outbox.answer_classification = classification
        outbox.connector_id = connector.id
        outbox.capability_version_id = capability_version_id
        outbox.connector_fingerprint = _fingerprint(connector)
        outbox.content_fingerprint = hashlib.sha256(text.encode()).hexdigest()
        outbox.status = DingTalkOutboxStatus.QUEUED
        outbox.blocked_reason = None
        await self._set_last_error(session, outbox, None)
        outbox.next_attempt_at = utc_now()
        outbox.lease_expires_at = None
        await self._audit(session, outbox, "QUEUED")

    async def _dispatch_inputs(
        self,
        session: AsyncSession,
        outbox: DingTalkRobotOutbox,
    ) -> (
        tuple[
            ImInboxMessage,
            Run,
            ImInstallation,
            ImInstallationBinding,
            Connector,
            str,
            UUID,
            Principal,
        ]
        | None
    ):
        row = await session.execute(
            select(ImInboxMessage, Run, ImInstallation, ImInstallationBinding, Connector)
            .join(Run, Run.id == ImInboxMessage.run_id)
            .join(ImInstallation, ImInstallation.id == ImInboxMessage.installation_id)
            .join(ImInstallationBinding, ImInstallationBinding.id == ImInboxMessage.binding_id)
            .join(Connector, Connector.id == ImInstallation.connector_id)
            .where(
                ImInboxMessage.id == outbox.inbox_message_id,
                ImInboxMessage.organization_id == outbox.organization_id,
                Run.id == outbox.run_id,
                Run.status == RunStatus.COMPLETED,
                ImInstallation.id == outbox.installation_id,
                ImInstallationBinding.id == outbox.binding_id,
                Connector.id == outbox.connector_id,
            )
            .execution_options(populate_existing=True)
        )
        loaded = row.one_or_none()
        if loaded is None or outbox.capability_version_id is None:
            return None
        inbox, run, installation, binding, connector = loaded._tuple()
        if not _queue_is_ready(
            inbox, installation, binding, connector, outbox.capability_version_id
        ):
            return None
        if _fingerprint(connector) != outbox.connector_fingerprint:
            return None
        try:
            text, classification = await _answer_for_run_metadata(
                session, outbox.organization_id, run.id
            )
            if inbox.conversation_type == "group":
                group_delivery = await _group_delivery(
                    session,
                    run,
                    inbox,
                    await load_principal_by_id(
                        session, outbox.organization_id, outbox.recipient_user_id
                    ),
                    text,
                    classification,
                )
                if group_delivery is None:
                    return None
                audience, delivery_mode, text = group_delivery
                if (
                    outbox.audience_id != audience.id
                    or outbox.audience_member_fingerprint != audience.member_fingerprint
                    or outbox.delivery_mode != delivery_mode
                    or outbox.recipient_conversation_id != inbox.conversation_id
                ):
                    return None
            if (
                hashlib.sha256(text.encode()).hexdigest() != outbox.content_fingerprint
                or outbox.answer_classification != classification
            ):
                return None
            principal = await load_principal_by_id(
                session, outbox.organization_id, outbox.recipient_user_id
            )
            await require_run_access(
                session,
                principal,
                run.id,
                source_content=True,
                source_corp_id=installation.external_corp_id or "",
            )
        except (AuthorizationError, NotFoundError, ValueError):
            return None
        return (
            inbox,
            run,
            installation,
            binding,
            connector,
            text,
            outbox.capability_version_id,
            principal,
        )

    async def _block(
        self,
        session: AsyncSession,
        outbox: DingTalkRobotOutbox,
        reason: str,
        error_code: str | None = None,
    ) -> None:
        outbox.status = DingTalkOutboxStatus.BLOCKED
        outbox.blocked_reason = reason
        await self._set_last_error(session, outbox, error_code or reason)
        outbox.next_attempt_at = None
        outbox.lease_expires_at = None
        await self._audit(session, outbox, "BLOCKED")

    @staticmethod
    async def _set_last_error(
        _session: AsyncSession, outbox: DingTalkRobotOutbox, value: str | None
    ) -> None:
        """Persist a catalog code after mapping a transport-only reason."""

        if value is None:
            mapped_code = None
        elif value == "delivery_lease_expired":
            mapped_code = "im_delivery_receipt_conflict"
        elif value == "delivery_preconditions_changed":
            mapped_code = "im_delivery_lineage_changed"
        elif value == "delivery_retry_exhausted":
            mapped_code = "dependency_failed"
        elif value == "delivery_answer_or_access_missing":
            mapped_code = "im_delivery_answer_missing"
        elif value == "delivery_configuration_missing":
            mapped_code = "credential_unavailable"
        elif value == "group_audience_denied":
            mapped_code = "im_delivery_denied"
        elif value in {
            "reconciliation_unavailable",
            "reconciliation_response_invalid",
            "reconciliation_status_unknown",
        }:
            mapped_code = "im_delivery_receipt_conflict"
        elif value == "capability_rate_limited":
            mapped_code = "capability_rate_limited"
        elif value == "authorization_fence_unavailable":
            mapped_code = "authorization_fence_unavailable"
        elif value == "connector_grant_missing":
            mapped_code = "connector_grant_missing"
        elif value == "credential_unavailable":
            mapped_code = "credential_unavailable"
        elif value == "capability_unavailable":
            mapped_code = "dependency_failed"
        elif value == "delivery_configuration_invalid":
            mapped_code = "connector_egress_invalid"
        elif value == "delivery_not_authorized":
            mapped_code = "im_delivery_denied"
        elif value == "delivery_gateway_unavailable":
            mapped_code = "dependency_failed"
        elif value == "delivery_precondition_failed":
            mapped_code = "im_delivery_lineage_changed"
        elif value == "unverified_send_response":
            mapped_code = "im_delivery_receipt_conflict"
        elif value == "recipient_rejected":
            mapped_code = "im_delivery_denied"
        elif value == "credential_in_text":
            mapped_code = "inline_secret_denied"
        elif value == "authentication_failed":
            mapped_code = "credential_unavailable"
        else:
            mapped_code = "dependency_failed"
        outbox.last_error_code = mapped_code

    async def _audit(
        self,
        session: AsyncSession,
        outbox: DingTalkRobotOutbox,
        outcome: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        audit_metadata: dict[str, object] = {
            "installation_id": str(outbox.installation_id),
            "status": outbox.status,
            "attempt_count": outbox.attempt_count,
            "fencing_token": outbox.fencing_token,
            "blocked_reason": outbox.blocked_reason,
            "last_error_code": outbox.last_error_code,
        }
        if metadata:
            audit_metadata.update(metadata)
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=outbox.organization_id,
                correlation_id=outbox.run_id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                action="im.dingtalk.outbox.dispatch",
                resource_type="dingtalk_robot_outbox",
                resource_id=str(outbox.id),
                outcome=outcome,
                metadata=audit_metadata,
            ),
        )


def _fingerprint(connector: Connector) -> str:
    return connector_fingerprint(
        connector_type=connector.connector_type,
        environment=connector.environment,
        endpoint=connector.endpoint,
        configuration=connector.configuration,
        credential_ref=connector.credential_ref,
        declared_grants=connector.declared_grants,
        allowed_egress=connector.allowed_egress,
    )


def _app_key(connector: Connector, installation: ImInstallation) -> str:
    return _configured_identity(
        connector,
        "app_key",
        fallback=installation.external_app_id,
    )


def _robot_code(connector: Connector, installation: ImInstallation) -> str:
    return _configured_identity(
        connector,
        "robot_code",
        fallback=_app_key(connector, installation),
    )


def _configured_identity(connector: Connector, key: str, *, fallback: str | None) -> str:
    """Resolve a public robot identity from an explicit env reference or config.

    App keys and robot codes are identifiers rather than secrets, but keeping
    their deployment-specific values in ``OBSION_*`` variables avoids baking a
    tenant identity into a database seed. Invalid references fail closed and
    are surfaced by the admin activation check.
    """

    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    env_key = f"{key}_env"
    configured_env = configuration.get(env_key)
    if configured_env is not None:
        if not isinstance(configured_env, str) or _ENV_NAME.fullmatch(configured_env) is None:
            return ""
        return os.environ.get(configured_env, "").strip()
    value = configuration.get(key, fallback)
    return value.strip() if isinstance(value, str) else ""


def _millis_to_datetime(value: int | None) -> datetime | None:
    """Convert a vendor epoch millisecond value without letting bad data abort reconciliation."""

    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _queue_is_ready(
    inbox: ImInboxMessage,
    installation: ImInstallation,
    binding: ImInstallationBinding,
    connector: Connector,
    capability_version_id: UUID | None,
) -> bool:
    return (
        inbox.conversation_type in {"direct", "group"}
        and installation.provider == "dingtalk"
        and installation.status == "ACTIVE"
        and binding.active
        and binding.id == inbox.binding_id
        and binding.user_id == inbox.subject_user_id
        and binding.sender_id == inbox.sender_id
        and connector.status == ConnectorStatus.ACTIVE
        and connector.connector_type == "dingtalk-robot"
        and connector.configuration.get("protocol") == _PROTOCOL
        and capability_version_id is not None
        and bool(_app_key(connector, installation).strip())
        and bool(_robot_code(connector, installation).strip())
    )


async def _bound_capability_version(
    session: AsyncSession,
    organization_id: UUID,
    connector_id: UUID,
    environment: str,
) -> UUID | None:
    value = await session.scalar(
        select(CapabilityVersion.id)
        .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.capability_id)
        .join(CapabilityBinding, CapabilityBinding.capability_version_id == CapabilityVersion.id)
        .where(
            CapabilityDefinition.organization_id == organization_id,
            CapabilityDefinition.name == _CAPABILITY,
            CapabilityDefinition.status == RegistryStatus.ACTIVE,
            CapabilityVersion.organization_id == organization_id,
            CapabilityVersion.risk_level == RiskLevel.L2,
            CapabilityVersion.side_effect == SideEffect.WRITE,
            CapabilityVersion.permission_action == "im.reply.deliver",
            CapabilityBinding.organization_id == organization_id,
            CapabilityBinding.connector_id == connector_id,
            CapabilityBinding.environment == environment,
            CapabilityBinding.enabled.is_(True),
        )
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )
    return value


async def _answer_for_run(session: AsyncSession, organization_id: UUID, run_id: UUID) -> str:
    text, _classification = await _answer_for_run_metadata(session, organization_id, run_id)
    return text


async def _answer_for_run_metadata(
    session: AsyncSession, organization_id: UUID, run_id: UUID
) -> tuple[str, str]:
    artifact = await session.scalar(
        select(Artifact)
        .where(
            Artifact.organization_id == organization_id,
            Artifact.run_id == run_id,
            Artifact.kind == ArtifactKind.TEXT,
            Artifact.title == "Obsion answer",
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    if artifact is None or not isinstance(artifact.inline_content, dict):
        raise ValueError("missing answer artifact")
    text = artifact.inline_content.get("markdown")
    if not isinstance(text, str) or not text.strip():
        text = artifact.inline_content.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("missing answer text")
    classification = artifact.classification.value
    if not isinstance(classification, str) or classification not in _CLASSIFICATION_RANK:
        raise ValueError("invalid answer classification")
    return text.strip(), classification


async def _load_group_audience_for_delivery(
    session: AsyncSession,
    outbox: DingTalkRobotOutbox,
    inbox: ImInboxMessage,
    run: Run,
    principal: Principal,
) -> ImGroupAudience | None:
    if (
        inbox.conversation_type != "group"
        or outbox.audience_id is None
        or outbox.recipient_conversation_id != inbox.conversation_id
    ):
        return None
    audience = await session.scalar(
        select(ImGroupAudience)
        .where(
            ImGroupAudience.id == outbox.audience_id,
            ImGroupAudience.organization_id == outbox.organization_id,
            ImGroupAudience.installation_id == outbox.installation_id,
            ImGroupAudience.conversation_id == inbox.conversation_id,
            ImGroupAudience.status == "ACTIVE",
        )
        .execution_options(populate_existing=True)
    )
    if audience is None or ensure_utc(audience.verified_until) <= utc_now():
        return None
    if audience.member_fingerprint != outbox.audience_member_fingerprint:
        return None
    try:
        configured_members = {UUID(str(value)) for value in audience.member_user_ids}
    except (TypeError, ValueError):
        return None
    if principal.id not in configured_members:
        return None
    workspace_id = await session.scalar(
        select(Thread.workspace_id)
        .join(Turn, Turn.thread_id == Thread.id)
        .where(
            Turn.id == run.turn_id,
            Thread.organization_id == outbox.organization_id,
        )
    )
    if workspace_id != audience.workspace_id:
        return None
    workspace_members = set(
        await session.scalars(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.organization_id == outbox.organization_id,
                WorkspaceMember.workspace_id == audience.workspace_id,
            )
        )
    )
    workspace_owner = await session.scalar(
        select(Workspace.owner_id).where(
            Workspace.id == audience.workspace_id,
            Workspace.organization_id == outbox.organization_id,
        )
    )
    if workspace_owner is not None:
        workspace_members.add(workspace_owner)
    if not configured_members.issubset(workspace_members):
        return None
    return audience


async def _group_delivery(
    session: AsyncSession,
    run: Run,
    inbox: ImInboxMessage,
    principal: Principal,
    text: str,
    classification: str,
) -> tuple[ImGroupAudience, str, str] | None:
    audience = await session.scalar(
        select(ImGroupAudience)
        .where(
            ImGroupAudience.organization_id == run.organization_id,
            ImGroupAudience.installation_id == inbox.installation_id,
            ImGroupAudience.conversation_id == inbox.conversation_id,
            ImGroupAudience.status == "ACTIVE",
        )
        .execution_options(populate_existing=True)
    )
    if audience is None or ensure_utc(audience.verified_until) <= utc_now():
        return None
    try:
        configured_members = {UUID(str(value)) for value in audience.member_user_ids}
    except (TypeError, ValueError):
        return None
    if principal.id not in configured_members:
        return None
    workspace_id = await session.scalar(
        select(Thread.workspace_id)
        .join(Turn, Turn.thread_id == Thread.id)
        .where(Turn.id == run.turn_id, Thread.organization_id == run.organization_id)
    )
    if workspace_id != audience.workspace_id:
        return None
    workspace_members = set(
        await session.scalars(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.organization_id == run.organization_id,
                WorkspaceMember.workspace_id == audience.workspace_id,
            )
        )
    )
    workspace_owner = await session.scalar(
        select(Workspace.owner_id).where(
            Workspace.id == audience.workspace_id,
            Workspace.organization_id == run.organization_id,
        )
    )
    if workspace_owner is not None:
        workspace_members.add(workspace_owner)
    if not configured_members.issubset(workspace_members):
        return None
    max_classification = audience.max_classification
    permitted = (
        audience.allow_final_answer
        and classification in _CLASSIFICATION_RANK
        and max_classification in _CLASSIFICATION_RANK
        and _CLASSIFICATION_RANK[classification] <= _CLASSIFICATION_RANK[max_classification]
    )
    if permitted:
        corp_id = await session.scalar(
            select(ImInstallation.external_corp_id).where(
                ImInstallation.id == inbox.installation_id,
                ImInstallation.organization_id == run.organization_id,
            )
        )
        guard = KnowledgePublicationGuard()
        for member_id in sorted(configured_members):
            try:
                member = await load_principal_by_id(session, run.organization_id, member_id)
                permitted = await guard.check(
                    session,
                    member,
                    [],
                    run_id=run.id,
                    stage="im_group_delivery",
                    expected_corp_id=corp_id or "",
                )
            except (AuthorizationError, NotFoundError):
                permitted = False
            if not permitted:
                break
    if permitted:
        return audience, "FINAL", text
    if audience.allow_status:
        return audience, "STATUS", _SAFE_GROUP_STATUS
    return None


def _retry_delay(attempt_count: int) -> int:
    return int(min(300, 5 * (2 ** min(max(attempt_count - 1, 0), 5))))
