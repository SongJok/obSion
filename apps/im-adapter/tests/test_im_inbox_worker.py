from __future__ import annotations

import asyncio
import io
from uuid import UUID

import httpx
import pytest

from obsion_im.config import ImError
from obsion_im.inbox import InboxClient, InboxSettings, run_inbox_worker
from obsion_im.main import main

INSTALLATION = UUID(int=100)
SETTINGS = InboxSettings(INSTALLATION, "http://127.0.0.1:8080", "cp-secret")


def receipt(index, processed=False):
    return {
        "id": str(UUID(int=index)),
        "installation_id": str(INSTALLATION),
        "vendor_event_id": f"event-{index}",
        "status": "PROCESSED" if processed else "RECEIVED",
        "turn_id": str(UUID(int=index + 1000)) if processed else None,
        "run_id": str(UUID(int=index + 2000)) if processed else None,
        "created_at": "2026-09-06T00:00:00Z",
        "processed_at": "2026-09-06T00:00:01Z" if processed else None,
    }


@pytest.mark.asyncio
async def test_restart_recovers_durable_messages_and_failures_do_not_starve_next_pages(caplog):
    pending = set(range(1, 26))
    calls = []
    fail = set(range(1, 21))

    def service(request):
        assert request.url.host == "127.0.0.1"
        assert request.headers["authorization"] == "Bearer cp-secret"
        if request.method == "GET":
            assert request.url.params["status"] == "RECEIVED"
            after = UUID(request.url.params.get("after", str(UUID(int=0)))).int
            limit = int(request.url.params["limit"])
            return httpx.Response(
                200, json=[receipt(i) for i in sorted(pending) if i > after][:limit]
            )
        assert request.url.path.endswith("/process")
        assert not request.content
        index = UUID(request.url.path.split("/")[-2]).int
        calls.append(index)
        if index in fail:
            return httpx.Response(403, json={"message": "正文-secret"})
        pending.discard(index)
        return httpx.Response(200, json=receipt(index, True))

    async def restart():
        client = InboxClient(SETTINGS, transport=httpx.MockTransport(service))
        try:
            return await run_inbox_worker(client, once=True)
        finally:
            await client.aclose()

    result = await restart()
    assert (result.processed, result.failed) == (5, 20)
    assert calls == list(range(1, 26))
    assert pending == set(range(1, 21))
    fail.clear()
    recovered = await restart()
    assert recovered.processed == 20
    assert not pending
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_process_response_lost_leaves_recovery_to_service_and_continues(caplog):
    pending = {1, 2}
    processed = set()

    def service(request):
        if request.method == "GET":
            return httpx.Response(200, json=[receipt(i) for i in sorted(pending)])
        index = UUID(request.url.path.split("/")[-2]).int
        processed.add(index)
        pending.discard(index)
        if index == 1:
            raise httpx.ReadTimeout("正文-secret token-secret", request=request)
        return httpx.Response(200, json=receipt(index, True))

    client = InboxClient(SETTINGS, transport=httpx.MockTransport(service))
    result = await run_inbox_worker(client, once=True)
    assert (result.failed, result.processed) == (1, 1)
    assert processed == {1, 2}
    assert (await run_inbox_worker(client, once=True)).processed == 0
    await client.aclose()
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_stop_between_events_and_pre_stopped_worker():
    stop = asyncio.Event()
    calls = []

    def service(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[receipt(1), receipt(2)])
        stop.set()
        return httpx.Response(200, json=receipt(1, True))

    client = InboxClient(SETTINGS, transport=httpx.MockTransport(service))
    result = await run_inbox_worker(client, stop=stop)
    assert result.processed == 1
    assert calls == ["GET", "POST"]
    await run_inbox_worker(client, stop=stop)
    assert calls == ["GET", "POST"]
    await client.aclose()


@pytest.mark.asyncio
async def test_stop_interrupts_poll_sleep():
    stop = asyncio.Event()
    client = InboxClient(
        SETTINGS, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    )
    task = asyncio.create_task(run_inbox_worker(client, stop=stop, poll_interval=100))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, 0.2)
    await client.aclose()


@pytest.mark.asyncio
async def test_continuous_bounded_round_preserves_cursor_past_poison_message():
    stop = asyncio.Event()
    calls = []

    def service(request):
        if request.method == "GET":
            after = UUID(request.url.params.get("after", str(UUID(int=0)))).int
            return httpx.Response(200, json=[receipt(after + 1)])
        index = UUID(request.url.path.split("/")[-2]).int
        calls.append(index)
        if index == 3:
            stop.set()
            return httpx.Response(200, json=receipt(index, True))
        return httpx.Response(500, json={})

    client = InboxClient(SETTINGS, transport=httpx.MockTransport(service))
    result = await run_inbox_worker(client, stop=stop, limit=1, max_events=1, poll_interval=0.001)
    assert calls == [1, 2, 3]
    assert result.failed == 2 and result.processed == 1
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, [receipt(2), receipt(1)], [receipt(1, True)]])
async def test_invalid_listing_stops_batch_without_effects(body):
    def service(request):
        assert request.method == "GET"
        return httpx.Response(200, json=body)

    client = InboxClient(SETTINGS, transport=httpx.MockTransport(service))
    assert (await run_inbox_worker(client, once=True)).polls_failed == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_propagates_without_logging(caplog):
    started = asyncio.Event()

    async def service(request):
        started.set()
        await asyncio.Event().wait()

    client = InboxClient(SETTINGS, transport=httpx.MockTransport(service))
    task = asyncio.create_task(run_inbox_worker(client))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.aclose()
    assert not caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/path",
        "https://user:secret@control.invalid",
        "http://remote.invalid",
        "https://control.invalid?token=secret",
        "file:///secret",
    ],
)
def test_only_fixed_control_plane_origin_allowed(url):
    with pytest.raises(ImError) as exc:
        InboxSettings(INSTALLATION, url, "token-secret")
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 21},
        {"max_events": 0},
        {"poll_interval": float("nan")},
        {"poll_interval": 0},
    ],
)
async def test_worker_bounds_validated(kwargs):
    with pytest.raises(ImError):
        await run_inbox_worker(None, once=True, **kwargs)


def test_worker_cli_once_reports_content_free_result(monkeypatch):
    def service(request):
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        "obsion_im.inbox.InboxClient",
        lambda settings: InboxClient(settings, transport=httpx.MockTransport(service)),
    )
    out, err = io.StringIO(), io.StringIO()
    assert (
        main(
            ["inbox-worker", "--installation-id", str(INSTALLATION), "--once"],
            environ={"OBSION_TOKEN": "cp-secret"},
            out=out,
            err=err,
        )
        == 0
    )
    assert out.getvalue() == '{"failed": 0, "polls_failed": 0, "processed": 0}\n'
    assert not err.getvalue()


@pytest.mark.asyncio
async def test_round_observation_is_content_free_and_observer_failure_nonfatal():
    stop = asyncio.Event()
    snapshots = []

    def observe(result):
        snapshots.append(result)
        if len(snapshots) == 2:
            stop.set()
        raise RuntimeError("observer-secret")

    client = InboxClient(
        SETTINGS, transport=httpx.MockTransport(lambda r: httpx.Response(503, json={}))
    )
    result = await run_inbox_worker(client, stop=stop, poll_interval=0.001, on_round=observe)
    assert [item.polls_failed for item in snapshots] == [1, 2]
    assert snapshots[0] is not result
    assert "secret" not in repr(snapshots)
    await client.aclose()
