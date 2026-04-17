"""Redis-backed summary-table response cache."""
from __future__ import annotations

import json

import pytest

from backend.cache.redis_table import DEFAULT_TTL_SECONDS, RedisTableCache


def test_table_key_shape() -> None:
    k = RedisTableCache.table_key("DS1", "subject", authed=False)
    assert k == "table:DS1:subject:public"
    k2 = RedisTableCache.table_key("DS1", "subject", authed=True)
    assert k2 == "table:DS1:subject:authed"


def test_default_ttl_is_one_hour() -> None:
    assert DEFAULT_TTL_SECONDS == 60 * 60


async def test_cache_miss_runs_producer_and_fills(fake_redis) -> None:
    cache = RedisTableCache(fake_redis, ttl_seconds=3600)
    calls = 0

    async def build() -> dict:
        nonlocal calls
        calls += 1
        return {"columns": [], "rows": [{"a": 1}]}

    value = await cache.get_or_compute("k1", build)
    assert calls == 1
    assert value == {"columns": [], "rows": [{"a": 1}]}

    # Key must be set with a TTL.
    raw = await fake_redis.get("k1")
    assert raw == json.dumps({"columns": [], "rows": [{"a": 1}]})
    ttl = await fake_redis.ttl("k1")
    assert 0 < ttl <= 3600


async def test_cache_hit_skips_producer(fake_redis) -> None:
    cache = RedisTableCache(fake_redis)
    await fake_redis.set("k1", json.dumps({"cached": True}))

    async def build() -> dict:
        raise AssertionError("producer should not run on cache hit")

    value = await cache.get_or_compute("k1", build)
    assert value == {"cached": True}


async def test_producer_error_propagates_and_nothing_written(fake_redis) -> None:
    cache = RedisTableCache(fake_redis)

    async def build() -> dict:
        raise RuntimeError("cloud down")

    with pytest.raises(RuntimeError, match="cloud down"):
        await cache.get_or_compute("k1", build)

    # Nothing should have been written.
    assert await fake_redis.get("k1") is None


async def test_corrupt_cache_entry_is_overwritten(fake_redis) -> None:
    cache = RedisTableCache(fake_redis)
    await fake_redis.set("k1", "not json{")

    async def build() -> dict:
        return {"columns": [], "rows": []}

    value = await cache.get_or_compute("k1", build)
    assert value == {"columns": [], "rows": []}
    # And the slot now holds valid JSON.
    parsed = json.loads(await fake_redis.get("k1"))
    assert parsed == {"columns": [], "rows": []}


async def test_redis_unavailable_falls_through_to_producer() -> None:
    """If redis raises on .get, the cache should still run the producer
    and try to write (write may fail silently; both are non-fatal).
    """
    class ExplodingRedis:
        async def get(self, key: str) -> None:  # noqa: ARG002 — test stub matches redis API
            raise ConnectionError("redis down")

        async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
            raise ConnectionError("redis down")

    cache = RedisTableCache(ExplodingRedis())  # type: ignore[arg-type]

    async def build() -> dict:
        return {"ok": True}

    value = await cache.get_or_compute("k1", build)
    assert value == {"ok": True}


async def test_invalidate_removes_entry(fake_redis) -> None:
    cache = RedisTableCache(fake_redis)
    await fake_redis.set("k1", json.dumps({"x": 1}))
    await cache.invalidate("k1")
    assert await fake_redis.get("k1") is None


async def test_concurrent_misses_run_producer_once_per_process(fake_redis) -> None:
    """Per-key lock prevents in-process thundering herd. Cross-replica
    stampede is tolerable — bounded to N-replica duplicate builds."""
    import asyncio
    cache = RedisTableCache(fake_redis)
    calls = 0

    async def build() -> dict:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)  # let the other coroutines queue on the lock
        return {"calls": calls}

    results = await asyncio.gather(*[cache.get_or_compute("k1", build) for _ in range(8)])
    assert calls == 1
    assert all(r == {"calls": 1} for r in results)
