import asyncio
import contextlib
import time
from unittest.mock import patch

import pytest

from backend.errors import RateLimited
from backend.middleware.rate_limit import Limit, RateLimiter


@pytest.mark.asyncio
async def test_allows_up_to_limit(fake_redis) -> None:  # type: ignore[no-untyped-def]
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=3, window_seconds=60)
    subject = RateLimiter.subject_for(None, "1.2.3.4")
    for _ in range(3):
        await limiter.check(limit, subject)


@pytest.mark.asyncio
async def test_raises_when_exceeded(fake_redis) -> None:  # type: ignore[no-untyped-def]
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=2, window_seconds=60)
    subject = RateLimiter.subject_for(None, "1.2.3.4")
    await limiter.check(limit, subject)
    await limiter.check(limit, subject)
    with pytest.raises(RateLimited):
        await limiter.check(limit, subject)


@pytest.mark.asyncio
async def test_different_subjects_isolated(fake_redis) -> None:  # type: ignore[no-untyped-def]
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=1, window_seconds=60)
    await limiter.check(limit, "u:a")
    await limiter.check(limit, "u:b")
    with pytest.raises(RateLimited):
        await limiter.check(limit, "u:a")


def test_subject_hashes_ip_but_preserves_user_id() -> None:
    anon = RateLimiter.subject_for(None, "127.0.0.1")
    assert anon.startswith("i:")
    assert "127.0.0.1" not in anon
    authed = RateLimiter.subject_for("u123", "127.0.0.1")
    assert authed == "u:u123"


# --- Hardening tests (PR-2: conditional ZADD + nonce members) ---


@pytest.mark.asyncio
async def test_rate_limiter_does_not_count_rejected_requests(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """Rejected requests must not inflate the sorted set.

    With cap=5, firing 20 requests should leave the ZSET at cap=5 (the 5
    admitted), not 20. Otherwise an attacker defeats the limiter by growing
    the set beyond any reasonable bound.
    """
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=5, window_seconds=60)
    subject = RateLimiter.subject_for(None, "9.9.9.9")

    admitted = 0
    rejected = 0
    for _ in range(20):
        try:
            await limiter.check(limit, subject)
            admitted += 1
        except RateLimited:
            rejected += 1

    assert admitted == 5
    assert rejected == 15

    # ZCARD is bounded by the cap, not by the total attempts.
    key = f"ratelimit:{limit.bucket}:{subject}"
    zcard = await fake_redis.zcard(key)
    assert zcard == 5, f"expected zcard bounded by cap=5, got {zcard}"


@pytest.mark.asyncio
async def test_rate_limit_key_cardinality_bounded(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """1000 distinct subjects → 1000 keys, but each key stays bounded.

    This is the memory-exhaustion DoS guard: individual keys must not grow
    beyond the cap, regardless of how many fake subjects are fabricated.
    """
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=3, window_seconds=60)

    # Simulate 1000 fabricated subjects, each firing 10 attempts.
    for i in range(1000):
        subject = f"u:fake-{i}"
        for _ in range(10):
            with contextlib.suppress(RateLimited):
                await limiter.check(limit, subject)

    # Each key is bounded by the cap (3), not by the 10 attempts per subject.
    for i in (0, 42, 500, 999):
        key = f"ratelimit:{limit.bucket}:u:fake-{i}"
        zcard = await fake_redis.zcard(key)
        assert zcard <= 3, f"subject {i} exceeded cap: {zcard}"


@pytest.mark.asyncio
async def test_concurrent_burst_respects_cap(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """Two coroutines hammering check() concurrently with cap=10.

    The two-pipeline split is not atomic at 2+ replicas, so a small drift is
    tolerable (documented in rate_limit.py). At a single worker / single
    Redis connection in fakeredis, we expect strict conformance.
    """
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=10, window_seconds=60)
    subject = RateLimiter.subject_for(None, "8.8.8.8")

    async def worker(n: int) -> int:
        admitted_local = 0
        for _ in range(n):
            try:
                await limiter.check(limit, subject)
                admitted_local += 1
            except RateLimited:
                pass
        return admitted_local

    results = await asyncio.gather(worker(20), worker(20))
    total_admitted = sum(results)

    # Tolerate small drift from non-atomic check-then-add at multi-worker scale.
    assert total_admitted <= 10 + 2, f"burst leaked past cap: {total_admitted}"
    assert total_admitted >= 10, f"burst under-admitted: {total_admitted}"


@pytest.mark.asyncio
async def test_zset_member_is_unique(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """Two requests at identical time.time() must create distinct ZSET members.

    Prior behaviour used `str(now)` as the member — if two requests shared a
    timestamp (common when time.time() has µs granularity and calls collide),
    the ZADD silently overwrote. The nonce suffix fixes that.

    We patch `time.time` inside rate_limit so both calls produce the same
    score, and assert ZCARD==2 WHILE the patch is still active (fakeredis
    applies wall-clock TTL evaluation; once the patch lifts, the fake past
    timestamp makes the key look expired).
    """
    limiter = RateLimiter(fake_redis)
    limit = Limit(bucket="reads", max_requests=10, window_seconds=60)
    subject = RateLimiter.subject_for(None, "7.7.7.7")
    key = f"ratelimit:{limit.bucket}:{subject}"

    fixed_now = time.time()  # anchor near real "now" so key TTL stays valid
    with patch("backend.middleware.rate_limit.time.time", return_value=fixed_now):
        await limiter.check(limit, subject)
        await limiter.check(limit, subject)
        zcard = await fake_redis.zcard(key)
        members = await fake_redis.zrange(key, 0, -1)

    assert zcard == 2, f"nonce-less members would have collapsed to 1, got {zcard}"
    # Both members share the score but have distinct nonces, so they coexist.
    assert len(set(members)) == 2, f"members collapsed: {members}"


@pytest.mark.asyncio
async def test_fallback_does_not_count_rejected_requests() -> None:
    """In-memory fallback path (redis=None) must also not inflate the buffer."""
    limiter = RateLimiter(None)
    limit = Limit(bucket="reads", max_requests=3, window_seconds=60)
    subject = "u:fallback-user"

    admitted = 0
    for _ in range(20):
        try:
            await limiter.check(limit, subject)
            admitted += 1
        except RateLimited:
            pass

    assert admitted == 3
    key = f"ratelimit:{limit.bucket}:{subject}"
    buf = limiter._fallback[key]
    assert len(buf) == 3, f"fallback buffer inflated past cap: {len(buf)}"
