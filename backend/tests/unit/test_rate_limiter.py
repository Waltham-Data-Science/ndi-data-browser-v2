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
