from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from obsion_im.channel import ImDeliveryReceipt, OutboundMessage
from obsion_im.config import ImDeliveryPreSendError, ImError
from obsion_im.outbox import ImOutboxWorker


@dataclass
class FakeRest:
    claims: list[dict[str, Any] | None]
    claim_calls: list[dict[str, str]] = field(default_factory=list)
    completed: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    failures: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    unknown: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def claim_im_delivery(self, *, channel: str, worker_id: str) -> dict[str, Any] | None:
        self.claim_calls.append({"channel": channel, "worker_id": worker_id})
        return self.claims.pop(0) if self.claims else None

    async def complete_im_delivery(
        self,
        delivery_id: str,
        *,
        vendor_message_id: str,
        claim_generation: int | None = None,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        self.completed.append(
            (
                delivery_id,
                {
                    "vendor_message_id": vendor_message_id,
                    "claim_generation": claim_generation,
                    "worker_id": worker_id,
                },
            )
        )
        return {}

    async def fail_im_delivery(
        self,
        delivery_id: str,
        *,
        failure_code: str = "vendor_request_failed",
        claim_generation: int | None = None,
        worker_id: str | None = None,
        retryable: bool = True,
        retry_after_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.failures.append(
            (
                delivery_id,
                {
                    "failure_code": failure_code,
                    "claim_generation": claim_generation,
                    "worker_id": worker_id,
                    "retryable": retryable,
                    "retry_after_seconds": retry_after_seconds,
                },
            )
        )
        return {}

    async def mark_im_delivery_unknown(
        self,
        delivery_id: str,
        *,
        claim_generation: int | None = None,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        self.unknown.append(
            (
                delivery_id,
                {
                    "claim_generation": claim_generation,
                    "worker_id": worker_id,
                },
            )
        )
        return {}


@dataclass(frozen=True)
class FakeSettings:
    token: str | None = "control-plane-token"


@dataclass
class FakeRuntime:
    rest: FakeRest
    settings: FakeSettings = field(default_factory=FakeSettings)


class RecordingChannel:
    name = "feishu"
    delivery = "feishu_http"

    def __init__(self, outcome: ImDeliveryReceipt | Exception) -> None:
        self.outcome = outcome
        self.messages: list[OutboundMessage] = []

    async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt:
        self.messages.append(message)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def health(self) -> dict[str, object]:
        return {"authenticated": True}

    async def aclose(self) -> None:
        return None


def _claim(**overrides: Any) -> dict[str, Any]:
    text = "The deployment completed."
    claim = {
        "id": "delivery-1",
        "run_id": "run-1",
        "thread_id": "thread-1",
        "conversation_id": "oc_ops",
        "reply_to_sender_id": "ou_alice",
        "text": text,
        "channel": "feishu",
        "content_fingerprint": hashlib.sha256(text.encode()).hexdigest(),
        "claim_generation": 7,
    }
    claim.update(overrides)
    return claim


def _worker(
    rest: FakeRest, channel: RecordingChannel, *, worker_id: str = "outbox-worker-1"
) -> ImOutboxWorker:
    return ImOutboxWorker(FakeRuntime(rest), channel, worker_id=worker_id)


@pytest.mark.asyncio
async def test_claimed_delivery_with_receipt_completes_using_claim_fencing() -> None:
    rest = FakeRest([_claim()])
    channel = RecordingChannel(ImDeliveryReceipt(vendor_message_id="om_1"))

    assert await _worker(rest, channel).run(once=True) is True

    assert rest.claim_calls == [{"channel": "feishu", "worker_id": "outbox-worker-1"}]
    assert channel.messages == [
        OutboundMessage(
            conversation_id="oc_ops",
            text="The deployment completed.",
            run_id="run-1",
            thread_id="thread-1",
            channel="feishu",
            reply_to_sender_id="ou_alice",
            delivery="feishu_http",
            delivery_id="delivery-1",
        )
    ]
    assert rest.completed == [
        (
            "delivery-1",
            {
                "vendor_message_id": "om_1",
                "claim_generation": 7,
                "worker_id": "outbox-worker-1",
            },
        )
    ]
    assert rest.failures == []
    assert rest.unknown == []


@pytest.mark.asyncio
async def test_pre_send_failure_is_recorded_as_a_retryable_failed_delivery() -> None:
    rest = FakeRest([_claim()])
    channel = RecordingChannel(ImDeliveryPreSendError("vendor authentication failed"))

    assert await _worker(rest, channel).run(once=True) is False

    assert rest.failures == [
        (
            "delivery-1",
            {
                "failure_code": "vendor_pre_send_failed",
                "claim_generation": 7,
                "worker_id": "outbox-worker-1",
                "retryable": True,
                "retry_after_seconds": None,
            },
        )
    ]
    assert rest.completed == []
    assert rest.unknown == []


@pytest.mark.asyncio
async def test_ambiguous_channel_exception_marks_delivery_unknown() -> None:
    rest = FakeRest([_claim()])
    channel = RecordingChannel(RuntimeError("connection ended after request write"))

    assert await _worker(rest, channel).run(once=True) is False

    assert rest.unknown == [
        (
            "delivery-1",
            {"claim_generation": 7, "worker_id": "outbox-worker-1"},
        )
    ]
    assert rest.completed == []
    assert rest.failures == []


@pytest.mark.asyncio
async def test_invalid_claim_fails_closed_without_calling_the_vendor() -> None:
    rest = FakeRest([_claim(channel="dingtalk")])
    channel = RecordingChannel(ImDeliveryReceipt(vendor_message_id="om_1"))

    with pytest.raises(ImError, match="claim lineage is inconsistent"):
        await _worker(rest, channel).run(once=True)

    assert channel.messages == []
    assert rest.completed == []
    assert rest.failures == []
    assert rest.unknown == []
