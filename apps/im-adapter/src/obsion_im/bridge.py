from __future__ import annotations

import contextlib
import hashlib
import os
import socket
from typing import Any

from obsion_cli.runtime import ExperienceRuntime, _answer_from
from obsion_im.channel import ImChannel, InboundMessage, OutboundMessage
from obsion_im.config import (
    LOCAL_DELIVERY,
    ImDeliveryOutcomeUnknownError,
    ImDeliveryPreSendError,
    ImDeliveryRejectedError,
    ImDeliveryRetryableError,
    ImError,
)
from obsion_im.replies import render_outbound


class ImBridge:
    """Maps one IM conversation onto one Thread after control-plane principal mapping."""

    def __init__(
        self,
        runtime: ExperienceRuntime,
        channel: ImChannel,
        *,
        workspace_name: str = "IM",
        worker_id: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.channel = channel
        self.workspace_name = workspace_name
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:im-bridge"

    async def handle(self, inbound: InboundMessage) -> OutboundMessage:
        text = inbound.text.strip()
        if not text:
            raise ImError("Inbound message requires text")
        conversation_id = inbound.conversation_id.strip()
        if not conversation_id:
            raise ImError("Inbound message requires a conversation id")
        sender_id = inbound.sender_id.strip()
        if not sender_id:
            raise ImError("Inbound message requires a stable sender id")
        if inbound.vendor_event_id is not None:
            return await self._accept_trusted_event(inbound, text, conversation_id, sender_id)
        accepted = await self.runtime.rest.create_im_message(
            channel=inbound.channel.strip() or self.channel.name,
            sender_id=sender_id,
            conversation_id=conversation_id,
            text=text,
            sender_display=inbound.sender_display,
        )
        run_id = str(accepted.get("run_id") or "")
        thread_id = str(accepted.get("thread_id") or "")
        if not run_id:
            raise ImError("IM ingest did not return a Run")
        _run, events = await self.runtime.wait_for_run(run_id)
        artifacts = await self.runtime.list_run_artifacts(run_id)
        answer = _answer_from(events, artifacts).strip()
        token = self.runtime.settings.token
        conversation_id_for_delivery = conversation_id
        delivery_id: str | None = None
        delivery_status = ""
        claim_generation: int | None = None
        claim_worker_id: str | None = None
        if self.channel.delivery != LOCAL_DELIVERY:
            prepared = await self.runtime.rest.prepare_im_delivery(
                run_id,
                worker_id=self.worker_id,
            )
            delivery_id = str(prepared.get("id") or "")
            prepared_text = prepared.get("text")
            prepared_channel = prepared.get("channel")
            prepared_conversation = prepared.get("conversation_id")
            prepared_fingerprint = prepared.get("content_fingerprint")
            delivery_status = str(prepared.get("status") or "")
            if (
                not delivery_id
                or not isinstance(prepared_text, str)
                or prepared_channel != inbound.channel
                or prepared_conversation != conversation_id
                or prepared_fingerprint != hashlib.sha256(prepared_text.encode()).hexdigest()
            ):
                raise ImError("Control-plane IM delivery authorization was inconsistent")
            answer = prepared_text
            conversation_id_for_delivery = str(prepared_conversation)
            if delivery_status not in {"SENT", "PENDING", "PROCESSING"}:
                raise ImError("Control-plane IM delivery requires reconciliation before sending")
            if delivery_status != "SENT":
                claim_generation = _claim_generation(prepared)
                # A modern control plane atomically leases the exact prepared
                # delivery. Legacy PENDING responses remain reportable without
                # fencing so existing adapters continue to work during rollout.
                if claim_generation is not None:
                    claim_worker_id = self.worker_id
        if token and token in answer:
            raise ImError("Refusing to send a credential in an IM reply")
        outbound = OutboundMessage(
            conversation_id=conversation_id_for_delivery,
            text=answer or "(no answer)",
            run_id=run_id,
            thread_id=thread_id,
            channel=inbound.channel.strip() or self.channel.name,
            reply_to_sender_id=sender_id,
            delivery=self.channel.delivery,
            delivery_id=delivery_id,
        )
        if delivery_id is not None and delivery_status == "SENT":
            return outbound
        if delivery_id is not None:
            try:
                receipt = await self.channel.reply(outbound)
                if receipt is None or not receipt.vendor_message_id.strip():
                    raise ImDeliveryOutcomeUnknownError(
                        "Live IM delivery did not return a vendor receipt"
                    )
            except ImDeliveryOutcomeUnknownError:
                await self.runtime.rest.mark_im_delivery_unknown(
                    delivery_id,
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                )
                raise
            except ImDeliveryRetryableError as exc:
                await self.runtime.rest.fail_im_delivery(
                    delivery_id,
                    failure_code="vendor_rate_limited",
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                    retryable=True,
                    retry_after_seconds=exc.retry_after_seconds,
                )
                raise
            except ImDeliveryRejectedError:
                await self.runtime.rest.fail_im_delivery(
                    delivery_id,
                    failure_code="vendor_request_rejected",
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                    retryable=False,
                )
                raise
            except ImDeliveryPreSendError:
                await self.runtime.rest.fail_im_delivery(
                    delivery_id,
                    failure_code="vendor_pre_send_failed",
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                    retryable=True,
                )
                raise
            except ImError:
                await self.runtime.rest.fail_im_delivery(
                    delivery_id,
                    failure_code="vendor_request_failed",
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                    retryable=True,
                )
                raise
            except Exception:
                await self.runtime.rest.mark_im_delivery_unknown(
                    delivery_id,
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                )
                raise
            try:
                await self.runtime.rest.complete_im_delivery(
                    delivery_id,
                    vendor_message_id=receipt.vendor_message_id,
                    claim_generation=claim_generation,
                    worker_id=claim_worker_id,
                )
            except Exception:
                # The vendor already accepted the message. Never turn a lost
                # completion acknowledgement into a retryable send failure.
                with contextlib.suppress(Exception):
                    if claim_generation is None:
                        await self.runtime.rest.fail_im_delivery(
                            delivery_id, failure_code="delivery_audit_failed"
                        )
                    else:
                        await self.runtime.rest.mark_im_delivery_unknown(
                            delivery_id,
                            claim_generation=claim_generation,
                            worker_id=claim_worker_id,
                        )
                raise ImError("IM delivery requires receipt reconciliation") from None
        else:
            await self.channel.reply(outbound)
        return outbound

    async def _accept_trusted_event(
        self,
        inbound: InboundMessage,
        text: str,
        conversation_id: str,
        sender_id: str,
    ) -> OutboundMessage:
        """Commit a tenant-scoped Inbox record and return without waiting for a Run.

        A Stream callback must acknowledge after persistence. The control-plane
        Inbox worker subsequently creates the private Harness task.
        """
        if (
            not inbound.installation_id
            or not inbound.corp_id
            or not inbound.app_key
            or not inbound.vendor_event_id
        ):
            raise ImError("Trusted IM events require installation, corp, and app identity")
        accepted = await self.runtime.rest.accept_trusted_im_event(
            channel=inbound.channel.strip() or self.channel.name,
            installation_id=inbound.installation_id,
            corp_id=inbound.corp_id,
            app_key=inbound.app_key,
            vendor_event_id=inbound.vendor_event_id,
            sender_id=sender_id,
            conversation_id=conversation_id,
            text=text,
            is_group=_is_group_event(inbound.vendor_event),
        )
        inbox_event_id = str(accepted.get("inbox_event_id") or "")
        if not inbox_event_id:
            raise ImError("Trusted IM ingress did not return an Inbox event id")
        return OutboundMessage(
            conversation_id=conversation_id,
            text="已接收，正在处理。",
            run_id=str(accepted.get("run_id") or inbox_event_id),
            thread_id="",
            channel=inbound.channel.strip() or self.channel.name,
            reply_to_sender_id=sender_id,
            delivery=self.channel.delivery,
        )


def outbound_as_dict(message: OutboundMessage) -> dict[str, Any]:
    return render_outbound(message)


def _is_group_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    data = event.get("data")
    if not isinstance(data, dict):
        return False
    return str(data.get("conversationType") or data.get("conversation_type") or "").lower() in {
        "2",
        "group",
    }


def _claim_generation(claim: dict[str, Any]) -> int | None:
    value = claim.get("claim_generation")
    status = str(claim.get("status") or "").upper()
    if type(value) is int and value >= 1:
        return value
    if status == "PENDING" and (value is None or type(value) is int and value == 0):
        return None
    if status == "SENT":
        return None
    if type(value) is not int or value < 1:
        raise ImError("Control-plane IM delivery claim is missing claim generation")
    return value


def _require_claim_lineage(claim: dict[str, Any], prepared: dict[str, Any]) -> None:
    for key in ("id", "run_id", "channel", "conversation_id", "text", "content_fingerprint"):
        if claim.get(key) != prepared.get(key):
            raise ImError("Control-plane IM delivery claim lineage is inconsistent")
