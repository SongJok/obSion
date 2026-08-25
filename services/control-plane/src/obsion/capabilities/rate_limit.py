import asyncio
from dataclasses import dataclass
from time import time
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RateLimitUnavailable(RuntimeError):
    """Raised when a required distributed rate-limit decision cannot be made."""


class CapabilityRateLimiter(Protocol):
    async def allow(self, key: str, limit: int | None = None) -> bool: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class _Window:
    number: int
    count: int


class InMemoryFixedWindowRateLimiter:
    def __init__(self, default_limit: int, *, window_seconds: int = 60) -> None:
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int | None = None) -> bool:
        resolved_limit = _resolve_limit(limit, self.default_limit)
        window_number = int(time()) // self.window_seconds
        async with self._lock:
            window = self._windows.get(key)
            if window is None or window.number != window_number:
                window = _Window(number=window_number, count=0)
                self._windows[key] = window
            window.count += 1
            return window.count <= resolved_limit

    async def aclose(self) -> None:
        self._windows.clear()


class RedisFixedWindowRateLimiter:
    _SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

    def __init__(
        self,
        redis_url: str,
        default_limit: int,
        *,
        window_seconds: int = 60,
        fail_closed: bool = True,
    ) -> None:
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self.fail_closed = fail_closed
        self._redis = Redis.from_url(redis_url, decode_responses=True)

    async def allow(self, key: str, limit: int | None = None) -> bool:
        resolved_limit = _resolve_limit(limit, self.default_limit)
        window_number = int(time()) // self.window_seconds
        redis_key = f"obsion:capability-rate:{window_number}:{key}"
        try:
            count = await self._redis.eval(self._SCRIPT, 1, redis_key, self.window_seconds + 1)
        except RedisError as exc:
            if self.fail_closed:
                raise RateLimitUnavailable(
                    "Distributed capability rate limiting is unavailable"
                ) from exc
            return True
        return int(count) <= resolved_limit

    async def aclose(self) -> None:
        await self._redis.aclose()


def _resolve_limit(limit: int | None, default: int) -> int:
    if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
        return limit
    return default
