from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError
from obsion.common.time import utc_now
from obsion.db.models import Artifact, ImDelivery, Run, Turn
from obsion.domain.enums import (
    ActorType,
    ArtifactKind,
    DecisionEffect,
    ImDeliveryStatus,
    RiskLevel,
    RunStatus,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput

IM_DELIVERY_CONTEXT_TYPE = "im_delivery"
IM_DELIVERY_ACTION = "im.reply.deliver"


class ImDeliveryService:
    """Authorizes final-answer delivery and records a durable vendor receipt."""

    def __init__(self, policy: PolicyEngine | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self.audit = AuditWriter()

    async def prepare(
        self,
        session: AsyncSession,
        principal: Principal,
        run_id: UUID,
    ) -> dict[str, Any]:
        # SQLite 忽略 FOR UPDATE，先获得写锁；PostgreSQL 使用下方行锁。
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(
                update(Run)
                .where(Run.id == run_id, Run.organization_id == principal.organization_id)
                .values(updated_at=Run.updated_at)
            )
        row = (
            await session.execute(
                select(Run, Turn)
                .join(Turn, Turn.id == Run.turn_id)
                .where(
                    Run.id == run_id,
                    Run.organization_id == principal.organization_id,
                    Turn.organization_id == principal.organization_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Run", run_id)
        run, turn = row._tuple()
        if run.status != RunStatus.COMPLETED:
            raise ConflictError(
                "im_delivery_run_not_completed",
                "Only a completed IM Run can be delivered",
                status=run.status,
            )
        context = _delivery_context(turn.context_refs)
        channel = context["channel"]
        conversation_id = context["conversation_id"]
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action=IM_DELIVERY_ACTION,
                resource={
                    "run_id": str(run.id),
                    "channel": channel,
                    "conversation_id": conversation_id,
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
        answer = await _answer_for_run(session, principal.organization_id, run.id)
        fingerprint = hashlib.sha256(answer.encode()).hexdigest()
        delivery = await session.scalar(
            select(ImDelivery).where(
                ImDelivery.organization_id == principal.organization_id,
                ImDelivery.run_id == run.id,
            )
        )
        if delivery is None:
            delivery = ImDelivery(
                organization_id=principal.organization_id,
                run_id=run.id,
                channel=channel,
                conversation_id=conversation_id,
                content_fingerprint=fingerprint,
                status=ImDeliveryStatus.PENDING,
                policy_decision_id=decision.id,
                requested_by=principal.id,
                attempt_count=1,
            )
            session.add(delivery)
        elif (
            delivery.channel != channel
            or delivery.conversation_id != conversation_id
            or delivery.content_fingerprint != fingerprint
        ):
            raise ConflictError(
                "im_delivery_lineage_changed",
                "The persisted IM delivery lineage does not match the completed Run",
            )
        elif delivery.status != ImDeliveryStatus.SENT:
            # prepare 提交后即可能发生外部发送；缺少回执不代表可以再次发送。
            raise ConflictError(
                "im_delivery_receipt_conflict",
                "IM delivery requires receipt reconciliation before any further send",
                status=delivery.status,
            )
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.prepare",
            outcome="SUCCESS",
        )
        return {
            "id": delivery.id,
            "run_id": delivery.run_id,
            "channel": delivery.channel,
            "conversation_id": delivery.conversation_id,
            "text": answer,
            "content_fingerprint": delivery.content_fingerprint,
            "idempotency_key": str(delivery.id),
            "status": delivery.status,
            "attempt_count": delivery.attempt_count,
        }

    async def complete(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
        *,
        vendor_message_id: str,
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
        delivery.status = ImDeliveryStatus.SENT
        delivery.vendor_message_id = message_id
        delivery.failure_code = None
        delivery.delivered_at = utc_now()
        delivery.updated_at = delivery.delivered_at
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.complete",
            outcome="SUCCESS",
        )
        return delivery

    async def fail(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery_id: UUID,
        *,
        failure_code: str,
    ) -> ImDelivery:
        delivery = await self._get_for_update(session, principal, delivery_id)
        await self._authorize_report(session, principal, delivery)
        if delivery.status == ImDeliveryStatus.SENT:
            return delivery
        # 旧 fail 接口没有证明厂商未接受请求，保守保留不确定效果。
        delivery.status = ImDeliveryStatus.UNKNOWN
        delivery.failure_code = failure_code
        delivery.updated_at = utc_now()
        await session.flush()
        await self._audit(
            session,
            principal,
            delivery,
            action="experience.im.delivery.fail",
            outcome="FAILED",
        )
        return delivery

    async def _get_for_update(
        self,
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

    async def _authorize_report(
        self,
        session: AsyncSession,
        principal: Principal,
        delivery: ImDelivery,
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
                "im_delivery_denied",
                "IM delivery receipt reporting is not permitted",
                reasons=list(decision.reason_codes),
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
                },
            ),
        )


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
            return {"channel": channel, "conversation_id": conversation_id}
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
