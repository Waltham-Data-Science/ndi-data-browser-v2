"""Circuit breaker state machine."""
from __future__ import annotations

import time

import pytest

from backend.clients.circuit_breaker import CircuitBreaker, CircuitOpen, State


@pytest.mark.asyncio
async def test_closed_allows_calls() -> None:
    b = CircuitBreaker(threshold=3, cooldown_seconds=1.0)
    for _ in range(5):
        await b.before_call()
        await b.record_success()
    assert b.state is State.CLOSED


@pytest.mark.asyncio
async def test_opens_after_threshold_failures() -> None:
    b = CircuitBreaker(threshold=3, cooldown_seconds=1.0)
    for _ in range(3):
        await b.before_call()
        await b.record_failure()
    assert b.state is State.OPEN
    with pytest.raises(CircuitOpen):
        await b.before_call()


@pytest.mark.asyncio
async def test_half_opens_after_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    b = CircuitBreaker(threshold=1, cooldown_seconds=0.05)
    await b.before_call()
    await b.record_failure()
    assert b.state is State.OPEN
    time.sleep(0.06)
    await b.before_call()  # should transition to half-open
    assert b.state is State.HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_reopens_on_failure() -> None:
    b = CircuitBreaker(threshold=1, cooldown_seconds=0.05)
    await b.before_call()
    await b.record_failure()
    time.sleep(0.06)
    await b.before_call()
    await b.record_failure()
    assert b.state is State.OPEN


@pytest.mark.asyncio
async def test_half_open_closes_on_success() -> None:
    b = CircuitBreaker(threshold=1, cooldown_seconds=0.05)
    await b.before_call()
    await b.record_failure()
    time.sleep(0.06)
    await b.before_call()
    await b.record_success()
    assert b.state is State.CLOSED
