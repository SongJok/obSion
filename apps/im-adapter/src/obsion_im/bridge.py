from __future__ import annotations

import hashlib
from typing import Any

from obsion_cli.runtime import ExperienceRuntime, _answer_from
from obsion_im.channel import ImChannel, InboundMessage, OutboundMessage
from obsion_im.config import LOCAL_DELIVERY, ImError
from obsion_im.replies import render_outbound


class ImBridge:
    """Maps one IM conversation onto one Thread after control-plane principal mapping."""

    def __init__(
        self,
        runtime: ExperienceRuntime,
        channel: ImChannel,
        *,
        workspace_name: str = "IM",
    ) -> None:
        self.runtime = runtime
        self.channel = channel
        self.workspace_name = workspace_name

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
        if self.channel.delivery != LOCAL_DELIVERY:
            prepared = await self.runtime.rest.prepare_im_delivery(run_id)
            delivery_id = str(prepared.get("id") or "")
            prepared_text = prepared.get("text")
            prepared_channel = prepared.get("channel")
            prepared_conversation = prepared.get("conversation_id")
            prepared_fingerprint = prepared.get("content_fingerprint")
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
            delivery_status = str(prepared.get("status") or "")
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
        if delivery_status == "SENT":
            return outbound
        try:
            receipt = await self.channel.reply(outbound)
            if delivery_id is not None and (receipt is None or not receipt.vendor_message_id):
                raise ImError("Live IM delivery did not return a vendor receipt")
        except Exception as delivery_error:
            if delivery_id is not None:
                try:
                    await self.runtime.rest.fail_im_delivery(delivery_id)
                except Exception as audit_error:
                    raise ImError(
                        "IM delivery failed and its failure receipt could not be recorded"
                    ) from audit_error
            raise delivery_error
        if delivery_id is not None:
            assert receipt is not None
            await self.runtime.rest.complete_im_delivery(
                delivery_id,
                vendor_message_id=receipt.vendor_message_id,
            )
        return outbound


def outbound_as_dict(message: OutboundMessage) -> dict[str, Any]:
    return render_outbound(message)
