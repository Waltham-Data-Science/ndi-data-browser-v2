"""Classic three-state circuit breaker.

CLOSED  -> OPEN after `threshold` consecutive failures
OPEN    -> HALF_OPEN after `cooldown` seconds
HALF_OPEN -> CLOSED on one success, -> OPEN on one failure
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum

from ..observability.logging import get_logger
from ..observability.metrics import circuit_breaker_state

log = get_logger(__name__)


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(Exception):
    pass


class CircuitBreaker:
    def __init__(self, threshold: int, cooldown_seconds: float) -> None:
        self.threshold = threshold
        self.cooldown = cooldown_seconds
        self._state = State.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()
        self._update_gauge()

    @property
    def state(self) -> State:
        return self._state

    def _update_gauge(self) -> None:
        circuit_breaker_state.set(
            {State.CLOSED: 1.0, State.HALF_OPEN: 0.5, State.OPEN: 0.0}[self._state],
        )

    async def before_call(self) -> None:
        async with self._lock:
            if self._state is State.OPEN:
                if self._opened_at is not None and time.time() - self._opened_at >= self.cooldown:
                    self._state = State.HALF_OPEN
                    self._update_gauge()
                    log.info("circuit_breaker.half_open")
                else:
                    raise CircuitOpen("Circuit breaker is open")

    async def record_success(self) -> None:
        async with self._lock:
            if self._state is State.HALF_OPEN:
                log.info("circuit_breaker.closed")
            self._state = State.CLOSED
            self._failures = 0
            self._opened_at = None
            self._update_gauge()

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._state is State.HALF_OPEN or self._failures >= self.threshold:
                self._state = State.OPEN
                self._opened_at = time.time()
                self._update_gauge()
                log.warning("circuit_breaker.opened", failures=self._failures)
