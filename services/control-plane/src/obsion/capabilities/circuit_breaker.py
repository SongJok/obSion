"""Fail-closed HTTP connector circuit breaker.

Repeated transport failures open the circuit for a cooldown window so a dead
dependency cannot keep sending traffic. Policy, grants, and egress checks still
run before this guard; the breaker never bypasses authorization.
"""

from __future__ import annotations

import time
from threading import Lock

from obsion.common.errors import ObsionError

type Authority = str | tuple[str, int]


class ConnectorCircuitOpenError(ObsionError):
    def __init__(self, authority: str) -> None:
        super().__init__(
            "capabilities_unavailable",
            "The connector circuit is open after repeated failures",
            status_code=503,
            details={"authority": authority},
        )


def _authority_key(authority: Authority) -> str:
    if isinstance(authority, tuple):
        return f"{authority[0]}:{authority[1]}"
    return authority


class ConnectorCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = Lock()

    def guard(self, authority: Authority) -> None:
        key = _authority_key(authority)
        now = time.monotonic()
        with self._lock:
            opened = self._opened_at.get(key)
            if opened is None:
                return
            if now - opened < self.cooldown_seconds:
                raise ConnectorCircuitOpenError(key)
            self._opened_at.pop(key, None)
            self._failures[key] = 0

    def record_success(self, authority: Authority) -> None:
        key = _authority_key(authority)
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)

    def record_failure(self, authority: Authority) -> None:
        key = _authority_key(authority)
        with self._lock:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.failure_threshold:
                self._opened_at[key] = time.monotonic()
