import hashlib
import json
from typing import Any

import httpx
import pytest

from obsion_cli.config import CliSettings
from obsion_cli.runtime import ExperienceRuntime
from obsion_im.bridge import ImBridge
from obsion_im.channel import ImDeliveryReceipt, InboundMessage, OutboundMessage
from obsion_im.config import ImError
from obsion_sdk import AsyncObsionClient


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["UNKNOWN", "FAILED", "RECONCILING", "", "SENT"])
async def test_bridge_never_sends_without_pending_permission(state: str) -> None:
    await _exercise_bridge(state=state, lose_receipt=False)


@pytest.mark.asyncio
async def test_bridge_reports_unknown_when_complete_response_is_lost() -> None:
    await _exercise_bridge(state="PENDING", lose_receipt=True)


async def _exercise_bridge(*, state: str, lose_receipt: bool) -> None:
    sends = 0
    reports: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/experience/im/messages"):
            return httpx.Response(202, json={"run_id": "run-1", "thread_id": "thread-1"})
        if path.endswith("/runs/run-1"):
            return httpx.Response(200, json={"id": "run-1", "status": "COMPLETED"})
        if path.endswith("/events") or path.endswith("/artifacts"):
            return httpx.Response(200, json=[])
        if path.endswith("/deliveries"):
            return httpx.Response(
                200,
                json={
                    "id": "delivery-1",
                    "run_id": "run-1",
                    "channel": "dingtalk",
                    "conversation_id": "cid-1",
                    "text": "answer",
                    "content_fingerprint": hashlib.sha256(b"answer").hexdigest(),
                    "status": state,
                },
            )
        if path.endswith("/complete"):
            raise httpx.ReadTimeout("complete response lost", request=request)
        if path.endswith("/fail"):
            reports.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "delivery-1", "status": "UNKNOWN"})
        return httpx.Response(404, json={"code": "resource_not_found", "message": path})

    class Channel:
        name = "dingtalk"
        delivery = "dingtalk-http"

        async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt:
            nonlocal sends
            sends += 1
            return ImDeliveryReceipt(vendor_message_id="real-vendor-id")

        async def health(self) -> dict[str, object]:
            return {}

        async def aclose(self) -> None:
            return None

    rest = AsyncObsionClient("http://obsion.example", transport=httpx.MockTransport(handler))
    runtime = ExperienceRuntime(
        CliSettings(base_url="http://obsion.example", token="", protocol="rest"), rest=rest
    )
    try:
        bridge = ImBridge(runtime, Channel())
        inbound = InboundMessage(
            channel="dingtalk", sender_id="sender-1", conversation_id="cid-1", text="question"
        )
        if state == "SENT":
            await bridge.handle(inbound)
        else:
            with pytest.raises(ImError, match="reconciliation"):
                await bridge.handle(inbound)
    finally:
        await runtime.aclose()
    assert sends == (1 if lose_receipt else 0)
    assert reports == ([{"failure_code": "delivery_audit_failed"}] if lose_receipt else [])
