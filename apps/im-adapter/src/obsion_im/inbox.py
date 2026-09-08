from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import httpx

from obsion_im.config import ImError


@dataclass(frozen=True, slots=True)
class InboxSettings:
    installation_id: UUID
    base_url: str
    token: str = field(repr=False)
    timeout_seconds: float = 0.8

    def __post_init__(self) -> None:
        try:
            url = httpx.URL(self.base_url)
            valid = (
                url.scheme in {"http", "https"}
                and bool(url.host)
                and not url.userinfo
                and not url.query
                and not url.fragment
                and url.path in {"", "/"}
                and (url.scheme == "https" or url.host in {"localhost", "127.0.0.1", "::1"})
            )
        except Exception:
            valid = False
        if not valid:
            raise ImError("Inbox 控制面地址须为 HTTPS origin（本地回环可用 HTTP）")
        if not isinstance(self.installation_id, UUID) or not self.token.strip():
            raise ImError("Inbox 需要固定 installation UUID 和 OBSION_TOKEN")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ImError("Inbox 请求超时必须为有限正数")


@dataclass(frozen=True, slots=True)
class InboxReceipt:
    id: UUID
    installation_id: UUID
    vendor_event_id: str
    status: str
    turn_id: UUID | None
    run_id: UUID | None


def parse_receipt(value: object, installation_id: UUID) -> InboxReceipt:
    # 只接受无正文的持久回执；不能把任意 202 当作提交成功。
    expected = {
        "id",
        "installation_id",
        "vendor_event_id",
        "status",
        "turn_id",
        "run_id",
        "created_at",
        "processed_at",
    }
    try:
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError
        event = value["vendor_event_id"]
        if not isinstance(event, str) or not event.strip() or len(event) > 255:
            raise ValueError
        installation = UUID(value["installation_id"])
        if installation != installation_id or value["status"] not in {"RECEIVED", "PROCESSED"}:
            raise ValueError
        created = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            raise ValueError
        turn = UUID(value["turn_id"]) if value["turn_id"] is not None else None
        run = UUID(value["run_id"]) if value["run_id"] is not None else None
        if value["status"] == "RECEIVED":
            if turn is not None or run is not None or value["processed_at"] is not None:
                raise ValueError
        else:
            processed = datetime.fromisoformat(value["processed_at"].replace("Z", "+00:00"))
            if turn is None or run is None or processed.tzinfo is None:
                raise ValueError
        return InboxReceipt(UUID(value["id"]), installation, event, value["status"], turn, run)
    except (ValueError, TypeError, AttributeError, KeyError):
        raise ImError("Inbox 返回无效持久回执；保留事件等待恢复") from None


class InboxClient:
    def __init__(
        self, settings: InboxSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.settings = settings
        self.path = f"/api/v1/experience/im/installations/{settings.installation_id}/inbox"
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={"Authorization": f"Bearer {settings.token}"},
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        status: int,
        json: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> object:
        try:
            response = await self._client.request(method, path, json=json, params=params)
            if response.status_code != status:
                raise ValueError
            return response.json()
        except Exception:
            # 不打印异常、响应或请求，它们可能含正文、token 或服务端回显。
            raise ImError("Inbox 请求未得到确定成功；保留事件等待恢复") from None

    async def receive(self, payload: dict[str, str]) -> InboxReceipt:
        value = await self._request("POST", self.path, status=202, json=payload)
        receipt = parse_receipt(value, self.settings.installation_id)
        if receipt.vendor_event_id != payload["vendor_event_id"]:
            raise ImError("Inbox 回执事件不匹配；保留事件等待恢复")
        return receipt

    async def pending(self, *, limit: int = 20, after: UUID | None = None) -> list[InboxReceipt]:
        params = {"limit": str(limit), "status": "RECEIVED"}
        if after is not None:
            params["after"] = str(after)
        value = await self._request("GET", self.path, status=200, params=params)
        if not isinstance(value, list) or len(value) > limit:
            raise ImError("Inbox 列表无效；等待下次恢复")
        receipts = [parse_receipt(item, self.settings.installation_id) for item in value]
        if any(item.status != "RECEIVED" for item in receipts):
            raise ImError("Inbox 列表包含非待处理事件")
        return receipts

    async def process(self, item: InboxReceipt) -> InboxReceipt:
        value = await self._request("POST", f"{self.path}/{item.id}/process", status=200)
        receipt = parse_receipt(value, self.settings.installation_id)
        if (receipt.id, receipt.vendor_event_id, receipt.status) != (
            item.id,
            item.vendor_event_id,
            "PROCESSED",
        ):
            raise ImError("Inbox 处理回执不匹配；等待下次恢复")
        return receipt


@dataclass(slots=True)
class WorkerResult:
    processed: int = 0
    failed: int = 0
    polls_failed: int = 0


async def run_inbox_worker(
    client: InboxClient,
    *,
    once: bool = False,
    stop: asyncio.Event | None = None,
    limit: int = 20,
    max_events: int = 200,
    poll_interval: float = 1.0,
    on_round: Callable[[WorkerResult], None] | None = None,
) -> WorkerResult:
    if not 1 <= limit <= 20 or max_events < 1:
        raise ImError("Inbox worker limit 必须为 1..20，max-events 必须为正数")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ImError("Inbox worker 轮询间隔必须为有限正数")
    stop = stop if stop is not None else asyncio.Event()
    result = WorkerResult()
    after: UUID | None = None
    while not stop.is_set():
        attempted = 0
        while attempted < max_events and not stop.is_set():
            batch_limit = min(limit, max_events - attempted)
            try:
                items = await client.pending(limit=batch_limit, after=after)
                previous = after
                for item in items:
                    if previous is not None and item.id.int <= previous.int:
                        raise ImError("Inbox 分页顺序无效")
                    previous = item.id
            except ImError:
                result.polls_failed += 1
                break
            if not items:
                after = None
                break
            for item in items:
                if stop.is_set():
                    break
                attempted += 1
                try:
                    await client.process(item)
                    result.processed += 1
                except ImError:
                    result.failed += 1
                # 成败都推进稳定游标；处理后列表收缩也不会跳过其他事件。
                after = item.id
            if len(items) < batch_limit:
                after = None
                break
        if on_round is not None:
            # 只提供计数快照；观察器失败不能删除事件或中断恢复。
            with contextlib.suppress(Exception):
                on_round(WorkerResult(result.processed, result.failed, result.polls_failed))
        if once:
            return result
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)
    return result
