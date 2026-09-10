from __future__ import annotations

import asyncio
import hashlib
import os
import socket
from typing import Any

from obsion_cli.runtime import ExperienceRuntime
from obsion_im.channel import ImChannel, OutboundMessage
from obsion_im.config import (
    LOCAL_DELIVERY,
    ImDeliveryOutcomeUnknownError,
    ImDeliveryPreSendError,
    ImDeliveryRejectedError,
    ImDeliveryRetryableError,
    ImError,
)


class ImOutboxWorker:
    """Lease authorized delivery facts and perform at most one vendor write per claim."""

    def __init__(
        self,
        runtime: ExperienceRuntime,
        channel: ImChannel,
        *,
        worker_id: str | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if channel.delivery == LOCAL_DELIVERY:
            raise ImError("The durable IM Outbox worker requires an explicit vendor transport")
        self.runtime = runtime
        self.channel = channel
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:im-outbox"
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)
        self._stop = asyncio.Event()

    async def run(self, *, once: bool = False) -> bool:
        delivered = False
        while not self._stop.is_set():
            claim = await self.runtime.rest.claim_im_delivery(
                channel=self.channel.name,
                worker_id=self.worker_id,
            )
            if claim is None:
                if once:
                    return delivered
                await self._wait()
                continue
            delivered = await self._deliver(claim) or delivered
            if once:
                return delivered
        return delivered

    def stop(self) -> None:
        self._stop.set()

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)
        except TimeoutError:
            return

    async def _deliver(self, claim: dict[str, Any]) -> bool:
        delivery_id = _required_text(claim, "id")
        run_id = _required_text(claim, "run_id")
        thread_id = _required_text(claim, "thread_id")
        conversation_id = _required_text(claim, "conversation_id")
        text = _required_text(claim, "text")
        channel = _required_text(claim, "channel")
        fingerprint = _required_text(claim, "content_fingerprint")
        generation = claim.get("claim_generation")
        if channel != self.channel.name or not isinstance(generation, int) or generation < 1:
            raise ImError("Control-plane IM Outbox claim lineage is inconsistent")
        if hashlib.sha256(text.encode()).hexdigest() != fingerprint:
            raise ImError("Control-plane IM Outbox content fingerprint is inconsistent")
        token = self.runtime.settings.token
        if token and token in text:
            raise ImError("Refusing to send a credential in an IM reply")
        sender = claim.get("reply_to_sender_id")
        message = OutboundMessage(
            conversation_id=conversation_id,
            text=text,
            run_id=run_id,
            thread_id=thread_id,
            channel=channel,
            reply_to_sender_id=sender if isinstance(sender, str) and sender else None,
            delivery=self.channel.delivery,
            delivery_id=delivery_id,
        )
        try:
            receipt = await self.channel.reply(message)
            if receipt is None or not receipt.vendor_message_id.strip():
                raise ImDeliveryOutcomeUnknownError(
                    "Live IM delivery did not return a vendor receipt"
                )
        except ImDeliveryOutcomeUnknownError:
            await self.runtime.rest.mark_im_delivery_unknown(
                delivery_id,
                claim_generation=generation,
                worker_id=self.worker_id,
            )
            return False
        except ImDeliveryRetryableError as exc:
            await self.runtime.rest.fail_im_delivery(
                delivery_id,
                failure_code="vendor_rate_limited",
                claim_generation=generation,
                worker_id=self.worker_id,
                retryable=True,
                retry_after_seconds=exc.retry_after_seconds,
            )
            return False
        except ImDeliveryRejectedError:
            await self.runtime.rest.fail_im_delivery(
                delivery_id,
                failure_code="vendor_request_rejected",
                claim_generation=generation,
                worker_id=self.worker_id,
                retryable=False,
            )
            return False
        except ImDeliveryPreSendError:
            await self.runtime.rest.fail_im_delivery(
                delivery_id,
                failure_code="vendor_pre_send_failed",
                claim_generation=generation,
                worker_id=self.worker_id,
                retryable=True,
            )
            return False
        except Exception:
            # Once the channel call starts, an unclassified failure is ambiguous.
            await self.runtime.rest.mark_im_delivery_unknown(
                delivery_id,
                claim_generation=generation,
                worker_id=self.worker_id,
            )
            return False
        await self.runtime.rest.complete_im_delivery(
            delivery_id,
            vendor_message_id=receipt.vendor_message_id,
            claim_generation=generation,
            worker_id=self.worker_id,
        )
        return True


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ImError(f"Control-plane IM Outbox claim is missing {key}")
    return value.strip()
