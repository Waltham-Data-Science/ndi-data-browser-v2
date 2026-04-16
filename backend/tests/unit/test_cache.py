import asyncio
import pytest

from backend.cache.ttl import AsyncTTLCache


@pytest.mark.asyncio
async def test_caches_value() -> None:
    c: AsyncTTLCache[int] = AsyncTTLCache(maxsize=4, ttl_seconds=60)
    calls = 0

    async def producer() -> int:
        nonlocal calls
        calls += 1
        return 42

    assert await c.get_or_compute("k", producer) == 42
    assert await c.get_or_compute("k", producer) == 42
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_requests_coalesce() -> None:
    c: AsyncTTLCache[int] = AsyncTTLCache(maxsize=4, ttl_seconds=60)
    calls = 0

    async def producer() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return 7

    results = await asyncio.gather(*[c.get_or_compute("k", producer) for _ in range(10)])
    assert all(r == 7 for r in results)
    # All 10 concurrent lookups should have triggered just one producer call.
    assert calls == 1


@pytest.mark.asyncio
async def test_ttl_expires() -> None:
    c: AsyncTTLCache[int] = AsyncTTLCache(maxsize=4, ttl_seconds=0.05)
    calls = 0

    async def producer() -> int:
        nonlocal calls
        calls += 1
        return 1

    await c.get_or_compute("k", producer)
    await asyncio.sleep(0.08)
    await c.get_or_compute("k", producer)
    assert calls == 2
