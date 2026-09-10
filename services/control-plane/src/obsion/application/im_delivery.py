from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.db.models import (
    Artifact,
    ImDelivery,
    ImDeliveryAttempt,
    ImInboxEvent,
    ImInstallation,
    ImPrincipalBinding,
    Organization,
    Run,
    Turn,
    User,
)
from obsion.domain.enums import (
    ActorType,
    ArtifactKind,
    DecisionEffect,
    ImDeliveryStatus,
    RiskLevel,
    RunStatus,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput

IM_DELIVERY_CONTEXT_TYPE = "im_delivery"
IM_DELIVERY_ACTION = "im.reply.deliver"
_CLAIMABLE_DELIVERY_STATUSES = {ImDeliveryStatus.PENDING, ImDeliveryStatus.FAILED}


class ImDeliveryService:
    """Own durable IM delivery authorization, leases, attempts and reconciliation."""

    def __init__(self, settings: Settings, policy: PolicyEngine | None = None) -> None:
        self.settings = settings
        self.policy = policy or PolicyEngine()
        self.audit = AuditWriter()

    async def prepare(
        self,
        session: AsyncSession,
        principal: Principal,
        run_id: UUID,
        *,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        stable_worker = worker_id.strip() if worker_id is not None else None
        if worker_id is not None and not stable_worker:
            raise ConflictError(
                "im_delivery_claim_invalid", "IM delivery claims require worker identity"
            )
        # SQLite ignores FOR UPDATE. Acquire a write lock before reading the
        # delivery so concurrent compatibility callers cannot both prepare it.
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.organization_id == principal.organization_id)
                .values(updated_at=Run.updated_at)
            )
        run, turn = await self._run_and_turn(session, principal.organization_id, run_id, lock=True)
        if turn.created_by != principal.id and not principal.can("im.delegate"):
            raise NotFoundError("Run", run_id)
        context, text, fingerprint, snapshot, decision_id = await self._authorized_payload(
            session, principal, run, turn
        )
        delivery = await session.scalar(
            select(ImDelivery)
            .where(
                ImDelivery.organization_id == principal.organization_id,
                ImDelivery.run_id == run.id,
            )
            .with_for_update()
        )
        now = utc_now()
        if delivery is None:
            delivery = ImDelivery(
                organization_id=principal.organization_id,
                run_id=run.id,
                channel=context["channel"],
                conversation_id=context["conversation_id"],
                content_fingerprint=fingerprint,
                status=ImDeliveryStatus.PENDING,
                policy_decision_id=decision_id,
                requested_by=principal.id,
                attempt_count=1,
                send_attempt_count=0,
                claim_generation=0,
                recipient_snapshot=snapshot,
            )
            session.add(delivery)
        else:
            self._require_lineage(delivery, context, fingerprint, snapshot)
            trusted_pending = (
                isinstance(delivery.recipient_snapshot, dict)
                and delivery.recipient_snapshot.get("kind") == "trusted"
                and delivery.status == ImDeliveryStatus.PENDING
                and delivery.send_attempt_count == 0
            )
            if (
                stable_worker is None
                and delivery.status != ImDeliveryStatus.SENT
                and not trusted_pending
            ):
                raise ConflictError(
                    "im_delivery_receipt_conflict",
                    "IM delivery requires receipt reconciliation before any further send",
                    status=delivery.status,
                )
            if delivery.status == ImDeliveryStatus.UNKNOWN:
                raise ConflictError(
                    "operator_invocation_outcome_unknown",
                    "The IM delivery outcome is unknown and requires reconciliation",
                )
            if delivery.status == ImDeliveryStatus.PROCESSING:
                raise ConflictError(
                    "im_delivery_in_progress",
                    "The IM delivery is owned by an active Outbox claim",
                )
            if delivery.send_attempt_count > 0 and delivery.status != ImDeliveryStatus.SENT:
                raise ConflictError(
                    "im_delivery_managed_by_outbox",
                    "The IM delivery retry is managed by the durable Outbox",
                )
            if delivery.status == ImDeliveryStatus.FAILED:
                # Compatibility for the legacy synchronous adapter. Once a delivery
                # has an Outbox attempt, only the leased worker may retry it.
                delivery.status = ImDeliveryStatus.PENDING
                delivery.attempt_count += 1
                delivery.failure_code = None
                delivery.next_attempt_at = None
            delivery.policy_decision_id = decision_id
            delivery.recipient_snapshot = delivery.recipient_snapshot or snapshot
            delivery.updated_at = now
        await session.flush()
        if stable_worker is not None and delivery.status != ImDeliveryStatus.SENT:
            self._claim_prepared_delivery(
                session,
                delivery,
                worker_id=stable_worker,
                policy_decision_id=decision_id,
                recipient_snapshot=snapshot,
                now=now,
            )
            await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.prepare",
            outcome="SUCCESS",
        )
        return _prepared_delivery(delivery, text)

    def _claim_prepared_delivery(
        self,
        session: AsyncSession,
        delivery: ImDelivery,
        *,
        worker_id: str,
        policy_decision_id: UUID,
        recipient_snapshot: dict[str, Any],
        now: datetime,
    ) -> None:
        """Fence a synchronous compatibility send with the same lease as Outbox."""
        delivery.status = ImDeliveryStatus.PROCESSING
        delivery.send_attempt_count = int(delivery.send_attempt_count or 0) + 1
        delivery.claim_generation = int(delivery.claim_generation or 0) + 1
        delivery.lease_owner = worker_id
        delivery.lease_expires_at = now + timedelta(seconds=self.settings.im_delivery_lease_seconds)
        delivery.last_attempt_at = now
        delivery.next_attempt_at = None
        delivery.failure_code = None
        delivery.reconciliation_required_at = None
        delivery.recipient_snapshot = delivery.recipient_snapshot or recipient_snapshot
        delivery.policy_decision_id = policy_decision_id
        delivery.updated_at = now
        session.add(
            ImDeliveryAttempt(
                organization_id=delivery.organization_id,
                delivery_id=delivery.id,
                ordinal=delivery.send_attempt_count,
                claim_generation=delivery.claim_generation,
                channel=delivery.channel,
                status=ImDeliveryStatus.PROCESSING,
                claimed_by=worker_id,
                lease_expires_at=delivery.lease_expires_at,
                policy_decision_id=policy_decision_id,
                recipient_snapshot=recipient_snapshot,
                content_fingerprint=delivery.content_fingerprint,
                idempotency_key=str(delivery.id),
            )
        )

    async def claim(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        channel: str,
        worker_id: str,
    ) -> dict[str, Any] | None:
        if not principal.can("im.delegate"):
            raise AuthorizationError("im_delivery_denied", "IM Outbox claiming is not permitted")
        normalized_channel = channel.strip().lower()
        stable_worker = worker_id.strip()
        if not normalized_channel or not stable_worker:
            raise ConflictError(
                "im_delivery_claim_invalid", "IM Outbox claims require channel and worker identity"
            )
        now = utc_now()
        await session.scalar(
            select(Organization.id)
            .where(Organization.id == principal.organization_id)
            .with_for_update()
        )
        await self._expire_one_claim(session, principal, normalized_channel, now)
        recent_attempts = await session.scalar(
            select(func.count(ImDeliveryAttempt.id)).where(
                ImDeliveryAttempt.organization_id == principal.organization_id,
                ImDeliveryAttempt.channel == normalized_channel,
                ImDeliveryAttempt.created_at >= now - timedelta(minutes=1),
            )
        )
        if int(recent_attempts or 0) >= self.settings.im_delivery_rate_limit_per_minute:
            return None
        delivery = await session.scalar(
            select(ImDelivery)
            .where(
                ImDelivery.organization_id == principal.organization_id,
                ImDelivery.channel == normalized_channel,
                ImDelivery.status.in_(_CLAIMABLE_DELIVERY_STATUSES),
                ImDelivery.send_attempt_count < self.settings.im_delivery_max_attempts,
                or_(
                    ImDelivery.status == ImDeliveryStatus.PENDING,
                    and_(
                        ImDelivery.status == ImDeliveryStatus.FAILED,
                        ImDelivery.next_attempt_at.is_not(None),
                        ImDelivery.next_attempt_at <= now,
                    ),
                ),
            )
            .order_by(ImDelivery.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if delivery is None:
            return None
        try:
            run, turn = await self._run_and_turn(
                session, principal.organization_id, delivery.run_id, lock=False
            )
            await load_principal_by_id(session, delivery.organization_id, turn.created_by)
            requester = await load_principal_by_id(
                session, delivery.organization_id, delivery.requested_by
            )
            if requester.id != turn.created_by and not requester.can("im.delegate"):
                raise NotFoundError("Run", run.id)
            context, text, fingerprint, snapshot, decision_id = await self._authorized_payload(
                session, requester, run, turn
            )
            self._require_lineage(delivery, context, fingerprint, snapshot)
        except (AuthorizationError, ConflictError, NotFoundError) as exc:
            delivery.status = ImDeliveryStatus.FAILED
            delivery.failure_code = exc.code
            delivery.next_attempt_at = None
            delivery.updated_at = now
            await self._audit(
                session,
                principal,
                delivery,
                action="experience.im.delivery.reject",
                outcome="DENIED",
            )
            return None
        delivery.recipient_snapshot = delivery.recipient_snapshot or snapshot
        delivery.policy_decision_id = decision_id
        delivery.status = ImDeliveryStatus.PROCESSING
        delivery.send_attempt_count += 1
        delivery.claim_generation += 1
        delivery.lease_owner = stable_worker
        delivery.lease_expires_at = now + timedelta(seconds=self.settings.im_delivery_lease_seconds)
        delivery.last_attempt_at = now
        delivery.next_attempt_at = None
        delivery.failure_code = None
        delivery.reconciliation_required_at = None
        delivery.updated_at = now
        attempt = ImDeliveryAttempt(
            organization_id=delivery.organization_id,
            delivery_id=delivery.id,
            ordinal=delivery.send_attempt_count,
            claim_generation=delivery.claim_generation,
            channel=delivery.channel,
            status=ImDeliveryStatus.PROCESSING,
            claimed_by=stable_worker,
            lease_expires_at=delivery.lease_expires_at,
            policy_decision_id=decision_id,
            recipient_snapshot=snapshot,
            content_fingerprint=delivery.content_fingerprint,
            idempotency_key=str(delivery.id),
        )
        session.add(attempt)
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.claim",
            outcome="SUCCESS",
        )
        return {
            "id": delivery.id,
            "run_id": delivery.run_id,
            "thread_id": turn.thread_id,
            "channel": delivery.channel,
            "conversation_id": delivery.conversation_id,
            "reply_to_sender_id": snapshot.get("sender_id"),
            "text": text,
            "content_fingerprint": delivery.content_fingerprint,
            "idempotency_key": str(delivery.id),
            "status": delivery.status,
            "attempt_count": delivery.attempt_count,
            "send_attempt_count": delivery.send_attempt_count,
            "claim_generation": delivery.claim_generation,
            "lease_expires_at": delivery.lease_expires_at,
        }

    async def complete(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
        *,
        vendor_message_id: str,
        claim_generation: int | None = None,
        worker_id: str | None = None,
    ) -> ImDelivery:
        delivery = await self._get_for_update(session, principal, delivery_id)
        await self._authorize_report(session, principal, delivery)
        message_id = vendor_message_id.strip()
        if not message_id or message_id == str(delivery.id):
            raise ConflictError(
                "im_delivery_receipt_conflict",
                "A real vendor receipt is required; local delivery ids are not receipts",
            )
        if delivery.status == ImDeliveryStatus.SENT:
            if delivery.vendor_message_id != message_id:
                raise ConflictError(
                    "im_delivery_receipt_conflict",
                    "The IM delivery already has a different vendor receipt",
                )
            return delivery
        attempt = await self._require_claim(
            session, delivery, claim_generation=claim_generation, worker_id=worker_id
        )
        now = utc_now()
        delivery.status = ImDeliveryStatus.SENT
        delivery.vendor_message_id = message_id
        delivery.failure_code = None
        delivery.delivered_at = now
        delivery.next_attempt_at = None
        delivery.reconciliation_required_at = None
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.updated_at = now
        if attempt is not None:
            attempt.status = ImDeliveryStatus.SENT
            attempt.vendor_message_id = message_id
            attempt.failure_code = None
            attempt.completed_at = now
            attempt.updated_at = now
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.complete",
            outcome="SUCCESS",
        )
        return delivery

    async def mark_unknown(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
        *,
        claim_generation: int | None = None,
        worker_id: str | None = None,
    ) -> ImDelivery:
        """Hold an ambiguous vendor outcome until explicit reconciliation."""
        delivery = await self._get_for_update(session, principal, delivery_id)
        await self._authorize_report(session, principal, delivery)
        if delivery.status == ImDeliveryStatus.SENT:
            return delivery
        if delivery.status == ImDeliveryStatus.UNKNOWN:
            return delivery
        attempt = await self._require_claim(
            session, delivery, claim_generation=claim_generation, worker_id=worker_id
        )
        now = utc_now()
        delivery.status = ImDeliveryStatus.UNKNOWN
        delivery.failure_code = "vendor_outcome_unknown"
        delivery.last_attempt_at = now
        delivery.next_attempt_at = None
        delivery.reconciliation_required_at = now
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        delivery.updated_at = now
        if attempt is not None:
            attempt.status = ImDeliveryStatus.UNKNOWN
            attempt.failure_code = "vendor_outcome_unknown"
            attempt.completed_at = now
            attempt.updated_at = now
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.unknown",
            outcome="UNKNOWN",
        )
        return delivery

    async def fail(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
        *,
        failure_code: str,
        claim_generation: int | None = None,
        worker_id: str | None = None,
        retryable: bool = True,
        retry_after_seconds: float | None = None,
    ) -> ImDelivery:
        delivery = await self._get_for_update(session, principal, delivery_id)
        await self._authorize_report(session, principal, delivery)
        if delivery.status == ImDeliveryStatus.SENT:
            return delivery
        if delivery.status == ImDeliveryStatus.UNKNOWN:
            raise ConflictError(
                "operator_invocation_outcome_unknown",
                "The IM delivery outcome is unknown and requires reconciliation",
            )
        attempt = await self._require_claim(
            session, delivery, claim_generation=claim_generation, worker_id=worker_id
        )
        now = utc_now()
        if attempt is None:
            # Legacy adapters cannot prove that an unclaimed vendor request had
            # no side effect. Hold the result until a receipt is reported.
            delivery.status = ImDeliveryStatus.UNKNOWN
            delivery.failure_code = failure_code
            delivery.last_attempt_at = now
            delivery.next_attempt_at = None
            delivery.reconciliation_required_at = now
            delivery.updated_at = now
            await session.flush()
            await self._audit(
                session,
                principal,
                delivery,
                action="experience.im.delivery.fail",
                outcome="UNKNOWN",
            )
            return delivery
        delivery.status = ImDeliveryStatus.FAILED
        delivery.failure_code = failure_code
        delivery.last_attempt_at = now
        delivery.lease_owner = None
        delivery.lease_expires_at = None
        if (
            attempt is not None
            and retryable
            and (delivery.send_attempt_count < self.settings.im_delivery_max_attempts)
        ):
            delay = self._retry_delay(delivery.send_attempt_count, retry_after_seconds)
            delivery.next_attempt_at = now + timedelta(seconds=delay)
        else:
            delivery.next_attempt_at = None
        delivery.updated_at = now
        if attempt is not None:
            attempt.status = ImDeliveryStatus.FAILED
            attempt.failure_code = failure_code
            attempt.completed_at = now
            attempt.updated_at = now
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.fail",
            outcome="FAILED",
        )
        return delivery

    async def reconcile(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
        *,
        outcome: str,
        evidence: str,
        vendor_message_id: str | None = None,
    ) -> ImDelivery:
        if not principal.can("identity.write"):
            raise AuthorizationError(
                "admin_access_denied", "IM delivery reconciliation requires an administrator"
            )
        delivery = await self._get_for_update(session, principal, delivery_id)
        normalized_outcome = outcome.strip().upper()
        await self._authorize_report(session, principal, delivery)
        proof = evidence.strip()
        receipt = (vendor_message_id or "").strip()
        if not proof or normalized_outcome not in {"SENT", "CONFIRMED_UNSENT"}:
            raise ConflictError(
                "im_delivery_reconciliation_invalid",
                "Reconciliation requires a supported outcome and evidence reference",
            )
        if delivery.status != ImDeliveryStatus.UNKNOWN:
            if (
                delivery.reconciliation_outcome == normalized_outcome
                and delivery.reconciliation_evidence == proof
                and (normalized_outcome != "SENT" or delivery.vendor_message_id == receipt)
            ):
                return delivery
            raise ConflictError(
                "im_delivery_reconciliation_invalid",
                "Only an UNKNOWN IM delivery can be reconciled",
            )
        if normalized_outcome == "SENT" and not receipt:
            raise ConflictError(
                "im_delivery_receipt_missing",
                "A vendor-issued receipt is required to reconcile a sent delivery",
            )
        if normalized_outcome == "CONFIRMED_UNSENT" and receipt:
            raise ConflictError(
                "im_delivery_reconciliation_invalid",
                "A confirmed-unsent reconciliation cannot include a vendor receipt",
            )
        attempt = await self._current_attempt(session, delivery)
        now = utc_now()
        delivery.reconciled_at = now
        delivery.reconciled_by = principal.id
        delivery.reconciliation_outcome = normalized_outcome
        delivery.reconciliation_evidence = proof
        delivery.reconciliation_required_at = None
        if normalized_outcome == "SENT":
            delivery.status = ImDeliveryStatus.SENT
            delivery.vendor_message_id = receipt
            delivery.delivered_at = now
            delivery.failure_code = None
            delivery.next_attempt_at = None
        else:
            delivery.status = ImDeliveryStatus.FAILED
            delivery.vendor_message_id = None
            delivery.delivered_at = None
            delivery.failure_code = "vendor_confirmed_unsent"
            delivery.next_attempt_at = (
                now
                if delivery.send_attempt_count < self.settings.im_delivery_max_attempts
                else None
            )
        delivery.updated_at = now
        if attempt is not None and attempt.status == ImDeliveryStatus.UNKNOWN:
            attempt.status = delivery.status
            attempt.vendor_message_id = receipt or None
            attempt.failure_code = None if receipt else "vendor_confirmed_unsent"
            attempt.reconciled_at = now
            attempt.reconciled_by = principal.id
            attempt.reconciliation_outcome = normalized_outcome
            attempt.reconciliation_evidence = proof
            attempt.updated_at = now
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.reconcile",
            outcome=normalized_outcome,
        )
        return delivery

    async def get(
        self, session: AsyncSession, principal: Principal, delivery_id: UUID
    ) -> ImDelivery:
        delivery = await session.scalar(
            select(ImDelivery).where(
                ImDelivery.id == delivery_id,
                ImDelivery.organization_id == principal.organization_id,
            )
        )
        if delivery is None:
            raise NotFoundError("IM delivery", delivery_id)
        _run, turn = await self._run_and_turn(
            session, principal.organization_id, delivery.run_id, lock=False
        )
        if turn.created_by != principal.id and not principal.can("im.delegate"):
            raise NotFoundError("IM delivery", delivery_id)
        return delivery

    async def list_attempts(
        self, session: AsyncSession, principal: Principal, delivery_id: UUID
    ) -> list[ImDeliveryAttempt]:
        await self.get(session, principal, delivery_id)
        return list(
            await session.scalars(
                select(ImDeliveryAttempt)
                .where(
                    ImDeliveryAttempt.organization_id == principal.organization_id,
                    ImDeliveryAttempt.delivery_id == delivery_id,
                )
                .order_by(ImDeliveryAttempt.ordinal)
            )
        )

    async def _expire_one_claim(
        self,
        session: AsyncSession,
        principal: Principal,
        channel: str,
        now: datetime,
    ) -> None:
        expired = await session.scalar(
            select(ImDelivery)
            .where(
                ImDelivery.organization_id == principal.organization_id,
                ImDelivery.channel == channel,
                ImDelivery.status == ImDeliveryStatus.PROCESSING,
                ImDelivery.lease_expires_at <= now,
            )
            .order_by(ImDelivery.lease_expires_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if expired is None:
            return
        attempt = await self._current_attempt(session, expired)
        expired.status = ImDeliveryStatus.UNKNOWN
        expired.failure_code = "delivery_lease_expired"
        expired.reconciliation_required_at = now
        expired.next_attempt_at = None
        expired.lease_owner = None
        expired.lease_expires_at = None
        expired.updated_at = now
        if attempt is not None and attempt.status == ImDeliveryStatus.PROCESSING:
            attempt.status = ImDeliveryStatus.UNKNOWN
            attempt.failure_code = "delivery_lease_expired"
            attempt.completed_at = now
            attempt.updated_at = now
        await self._audit(
            session,
            principal,
            expired,
            action="experience.im.delivery.lease_expired",
            outcome="UNKNOWN",
        )

    async def _authorized_payload(
        self,
        session: AsyncSession,
        principal: Principal,
        run: Run,
        turn: Turn,
    ) -> tuple[dict[str, str], str, str, dict[str, Any], UUID]:
        if run.status != RunStatus.COMPLETED:
            raise ConflictError(
                "im_delivery_run_not_completed",
                "Only a completed IM Run can be delivered",
                status=run.status,
            )
        context = _delivery_context(turn.context_refs)
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action=IM_DELIVERY_ACTION,
                resource={
                    "run_id": str(run.id),
                    "channel": context["channel"],
                    "conversation_id": context["conversation_id"],
                },
                context={"environment": "production", "source": "im-final-answer"},
                risk_level=RiskLevel.L1,
                resource_type="im_delivery",
                agent_name="experience-im",
                run_id=run.id,
            ),
        )
        if decision.effect != DecisionEffect.ALLOW:
            raise AuthorizationError(
                "im_delivery_denied",
                "IM final-answer delivery is not permitted",
                reasons=list(decision.reason_codes),
            )
        snapshot = await self._recipient_snapshot(session, run, turn, context)
        answer = await _answer_for_run(session, principal.organization_id, run.id)
        text = _safe_group_status(run.id) if snapshot["status_only"] else answer
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        return context, text, fingerprint, snapshot, decision.id

    async def _recipient_snapshot(
        self, session: AsyncSession, run: Run, turn: Turn, context: dict[str, str]
    ) -> dict[str, Any]:
        event = await session.scalar(
            select(ImInboxEvent).where(
                ImInboxEvent.run_id == run.id,
                ImInboxEvent.organization_id == run.organization_id,
            )
        )
        if event is None:
            if any(
                isinstance(item, dict) and "inbox_event_id" in item for item in turn.context_refs
            ):
                raise AuthorizationError("im_delivery_denied", "Trusted IM lineage is missing")
            return {
                "kind": "legacy",
                "subject_user_id": str(turn.created_by),
                "conversation_id": context["conversation_id"],
                "sender_id": context.get("sender_id") or None,
                "status_only": context.get("group_status_only") == "true",
            }
        active = await session.scalar(
            select(ImPrincipalBinding.id)
            .join(ImInstallation, ImInstallation.id == ImPrincipalBinding.installation_id)
            .join(User, User.id == ImPrincipalBinding.user_id)
            .where(
                ImPrincipalBinding.id == event.binding_id,
                ImPrincipalBinding.organization_id == event.organization_id,
                ImPrincipalBinding.installation_id == event.installation_id,
                ImPrincipalBinding.sender_id == event.sender_id,
                ImPrincipalBinding.channel == event.channel,
                ImPrincipalBinding.user_id == turn.created_by,
                ImPrincipalBinding.active.is_(True),
                ImInstallation.organization_id == event.organization_id,
                ImInstallation.active.is_(True),
                User.organization_id == event.organization_id,
                User.active.is_(True),
            )
        )
        if (
            active is None
            or event.event_metadata.get("subject_user_id") != str(turn.created_by)
            or event.channel != context["channel"]
            or event.conversation_id != context["conversation_id"]
        ):
            raise AuthorizationError("im_delivery_denied", "Trusted IM audience is no longer valid")
        return {
            "kind": "trusted",
            "inbox_event_id": str(event.id),
            "installation_id": str(event.installation_id),
            "binding_id": str(event.binding_id),
            "audience_id": str(event.audience_id) if event.audience_id else None,
            "subject_user_id": str(turn.created_by),
            "conversation_id": event.conversation_id,
            "sender_id": event.sender_id,
            "status_only": event.is_group,
        }

    @staticmethod
    def _require_lineage(
        delivery: ImDelivery,
        context: dict[str, str],
        fingerprint: str,
        snapshot: dict[str, Any],
    ) -> None:
        if (
            delivery.channel != context["channel"]
            or delivery.conversation_id != context["conversation_id"]
            or delivery.content_fingerprint != fingerprint
            or (delivery.recipient_snapshot is not None and delivery.recipient_snapshot != snapshot)
        ):
            raise ConflictError(
                "im_delivery_lineage_changed",
                "The persisted IM delivery lineage does not match the completed Run",
            )

    @staticmethod
    async def _run_and_turn(
        session: AsyncSession,
        organization_id: UUID,
        run_id: UUID,
        *,
        lock: bool,
    ) -> tuple[Run, Turn]:
        statement = (
            select(Run, Turn)
            .join(Turn, Turn.id == Run.turn_id)
            .where(
                Run.id == run_id,
                Run.organization_id == organization_id,
                Turn.organization_id == organization_id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).one_or_none()
        if row is None:
            raise NotFoundError("Run", run_id)
        return row._tuple()

    @staticmethod
    async def _get_for_update(
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
    ) -> ImDelivery:
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(
                update(ImDelivery)
                .where(
                    ImDelivery.id == delivery_id,
                    ImDelivery.organization_id == principal.organization_id,
                )
                .values(updated_at=ImDelivery.updated_at)
            )
        delivery = await session.scalar(
            select(ImDelivery)
            .where(
                ImDelivery.id == delivery_id,
                ImDelivery.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if delivery is None:
            raise NotFoundError("IM delivery", delivery_id)
        return delivery

    async def _require_claim(
        self,
        session: AsyncSession,
        delivery: ImDelivery,
        *,
        claim_generation: int | None,
        worker_id: str | None,
    ) -> ImDeliveryAttempt | None:
        if delivery.status != ImDeliveryStatus.PROCESSING:
            if (
                claim_generation is None
                and delivery.claim_generation == 0
                and delivery.status
                in {ImDeliveryStatus.PENDING, ImDeliveryStatus.FAILED, ImDeliveryStatus.UNKNOWN}
            ):
                return None
            raise ConflictError(
                "im_delivery_claim_stale", "The IM delivery claim is no longer current"
            )
        if (
            claim_generation is None
            or worker_id is None
            or delivery.claim_generation != claim_generation
            or delivery.lease_owner != worker_id.strip()
            or delivery.lease_expires_at is None
            or ensure_utc(delivery.lease_expires_at) <= utc_now()
        ):
            raise ConflictError(
                "im_delivery_claim_stale", "The IM delivery claim is missing, stale, or expired"
            )
        attempt = await self._current_attempt(session, delivery)
        if (
            attempt is None
            or attempt.status != ImDeliveryStatus.PROCESSING
            or attempt.claim_generation != delivery.claim_generation
            or attempt.claimed_by != delivery.lease_owner
        ):
            raise ConflictError(
                "im_delivery_claim_stale", "The IM delivery attempt does not match its claim"
            )
        return attempt

    @staticmethod
    async def _current_attempt(
        session: AsyncSession, delivery: ImDelivery
    ) -> ImDeliveryAttempt | None:
        if delivery.send_attempt_count <= 0:
            return None
        return cast(
            ImDeliveryAttempt | None,
            await session.scalar(
                select(ImDeliveryAttempt)
                .where(
                    ImDeliveryAttempt.organization_id == delivery.organization_id,
                    ImDeliveryAttempt.delivery_id == delivery.id,
                    ImDeliveryAttempt.ordinal == delivery.send_attempt_count,
                )
                .with_for_update()
            ),
        )

    async def _authorize_report(
        self, session: AsyncSession, principal: Principal, delivery: ImDelivery
    ) -> None:
        if delivery.requested_by != principal.id:
            raise AuthorizationError(
                "im_delivery_denied", "Only the delivery requester can report its receipt"
            )
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action=IM_DELIVERY_ACTION,
                resource={
                    "run_id": str(delivery.run_id),
                    "channel": delivery.channel,
                    "conversation_id": delivery.conversation_id,
                },
                context={"environment": "production", "source": "im-delivery-receipt"},
                risk_level=RiskLevel.L1,
                resource_type="im_delivery",
                agent_name="experience-im",
                run_id=delivery.run_id,
            ),
        )
        if decision.effect != DecisionEffect.ALLOW:
            raise AuthorizationError(
                "im_delivery_denied", "IM delivery receipt reporting is not permitted"
            )

    def _retry_delay(self, attempt: int, retry_after_seconds: float | None) -> float:
        exponential = min(
            self.settings.im_delivery_max_backoff_seconds,
            self.settings.im_delivery_base_backoff_seconds * (2 ** max(0, attempt - 1)),
        )
        requested = max(0.0, retry_after_seconds or 0.0)
        return float(
            min(self.settings.im_delivery_max_backoff_seconds, max(exponential, requested))
        )

    async def _audit(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery: ImDelivery,
        *,
        action: str,
        outcome: str,
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=delivery.run_id,
                actor_type=ActorType.SERVICE,
                actor_id=principal.id,
                action=action,
                resource_type="im_delivery",
                resource_id=str(delivery.id),
                outcome=outcome,
                metadata={
                    "run_id": str(delivery.run_id),
                    "channel": delivery.channel,
                    "status": delivery.status,
                    "attempt_count": delivery.attempt_count,
                    "send_attempt_count": delivery.send_attempt_count,
                    "claim_generation": delivery.claim_generation,
                },
            ),
        )


def _prepared_delivery(delivery: ImDelivery, text: str) -> dict[str, Any]:
    return {
        "id": delivery.id,
        "run_id": delivery.run_id,
        "channel": delivery.channel,
        "conversation_id": delivery.conversation_id,
        "text": text,
        "content_fingerprint": delivery.content_fingerprint,
        "idempotency_key": str(delivery.id),
        "status": delivery.status,
        "attempt_count": delivery.attempt_count,
        "send_attempt_count": delivery.send_attempt_count,
        "claim_generation": delivery.claim_generation,
    }


def _delivery_context(context_refs: object) -> dict[str, str]:
    if not isinstance(context_refs, list):
        raise ConflictError(
            "im_delivery_context_missing",
            "The completed Run does not have durable IM delivery context",
        )
    for item in context_refs:
        if not isinstance(item, dict) or item.get("type") != IM_DELIVERY_CONTEXT_TYPE:
            continue
        channel = item.get("channel")
        conversation_id = item.get("conversation_id")
        if (
            isinstance(channel, str)
            and channel
            and isinstance(conversation_id, str)
            and conversation_id
        ):
            group_status_only = item.get("group_status_only") is True
            sender_id = item.get("sender_id")
            return {
                "channel": channel,
                "conversation_id": conversation_id,
                "group_status_only": "true" if group_status_only else "false",
                "sender_id": sender_id if isinstance(sender_id, str) else "",
            }
    raise ConflictError(
        "im_delivery_context_missing",
        "The completed Run does not have durable IM delivery context",
    )


async def _answer_for_run(
    session: AsyncSession,
    organization_id: UUID,
    run_id: UUID,
) -> str:
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
        raise ConflictError(
            "im_delivery_answer_missing",
            "The completed Run does not have a durable answer artifact",
        )
    value = artifact.inline_content.get("markdown")
    if not isinstance(value, str) or not value.strip():
        value = artifact.inline_content.get("text")
    if not isinstance(value, str) or not value.strip():
        raise ConflictError(
            "im_delivery_answer_missing",
            "The completed Run answer artifact has no deliverable text",
        )
    return value.strip()


def _safe_group_status(run_id: UUID) -> str:
    """Do not copy user-scoped output to a group before an audience proof exists."""
    return f"任务已完成。请在 Obsion 工作台查看结果：/runs/{run_id}"
