"""Redis-backed sliding-window rate limiter.

Uses a sorted set per (bucket, subject) keyed by timestamp. O(log N) per request.
Falls back to in-memory counters if Redis is unavailable, with a warn log.
"""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass

from redis.asyncio import Redis

from ..errors import AuthRateLimited, RateLimited
from ..observability.logging import get_logger
from ..observability.metrics import rate_limit_rejections_total

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class Limit:
    bucket: str        # e.g. "reads", "query", "login-ip"
    max_requests: int
    window_seconds: int
    # If True, exceeds raise AuthRateLimited (distinct code); else RateLimited.
    auth_bucket: bool = False


class RateLimiter:
    def __init__(self, redis: Redis | None) -> None:
        self.redis = redis
        self._fallback: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def subject_for(user_id: str | None, ip: str) -> str:
        # Never log raw IPs; hash for bucket keys.
        if user_id:
            return f"u:{user_id}"
        return "i:" + hashlib.sha256(ip.encode()).hexdigest()[:16]

    async def check(self, limit: Limit, subject: str) -> None:
        key = f"ratelimit:{limit.bucket}:{subject}"
        now = time.time()
        window_start = now - limit.window_seconds

        allowed = True
        retry_after = 1

        if self.redis is not None:
            try:
                # Trim, count, add, expire — in a pipeline for atomicity.
                pipe = self.redis.pipeline()
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                pipe.zadd(key, {str(now): now})
                pipe.expire(key, limit.window_seconds + 1)
                _, count, _, _ = await pipe.execute()
                if count >= limit.max_requests:
                    allowed = False
                    oldest = await self.redis.zrange(key, 0, 0, withscores=True)
                    if oldest:
                        retry_after = max(1, int(oldest[0][1] + limit.window_seconds - now))
            except Exception as e:  # noqa: BLE001
                log.warning("rate_limit.redis_error", error=str(e))
                # Fall through to in-memory.
                allowed = self._fallback_check(key, now, window_start, limit.max_requests)
        else:
            allowed = self._fallback_check(key, now, window_start, limit.max_requests)

        if not allowed:
            rate_limit_rejections_total.labels(bucket=limit.bucket).inc()
            if limit.auth_bucket:
                err = AuthRateLimited(
                    f"Too many attempts. Please wait {retry_after} seconds.",
                    details={"retry_after_seconds": retry_after},
                )
            else:
                err = RateLimited(
                    f"Rate limit exceeded. Please wait {retry_after} seconds.",
                    details={"retry_after_seconds": retry_after},
                )
            raise err

    def _fallback_check(self, key: str, now: float, window_start: float, cap: int) -> bool:
        buf = self._fallback[key]
        # Purge.
        while buf and buf[0] < window_start:
            buf.pop(0)
        if len(buf) >= cap:
            return False
        buf.append(now)
        return True
