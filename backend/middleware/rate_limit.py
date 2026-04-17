"""Redis-backed sliding-window rate limiter.

Uses a sorted set per (bucket, subject) keyed by timestamp. O(log N) per request.
Falls back to in-memory counters if Redis is unavailable, with a warn log.
"""
from __future__ import annotations

import hashlib
import time
import uuid
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

        # NOTE: two-pipeline check-then-add is non-atomic. Acceptable at 1-2
        # replicas. If scale grows or per-user bucket precision matters, move
        # to a single Lua script (see backend/middleware/rate_limit_lua.py
        # TODO — not yet written).
        allowed = True
        retry_after = 1

        if self.redis is not None:
            try:
                # Step 1: trim expired entries + read current count.
                check_pipe = self.redis.pipeline()
                check_pipe.zremrangebyscore(key, 0, window_start)
                check_pipe.zcard(key)
                _, count = await check_pipe.execute()

                if count >= limit.max_requests:
                    # Reject WITHOUT adding — prevents rejected requests from
                    # inflating the sorted set and enabling a memory-exhaustion
                    # DoS via fabricated subjects.
                    allowed = False
                    oldest = await self.redis.zrange(key, 0, 0, withscores=True)
                    if oldest:
                        retry_after = max(1, int(oldest[0][1] + limit.window_seconds - now))
                else:
                    # Step 2: only admitted requests add a member. Nonce avoids
                    # the duplicate-score silent-overwrite bug when multiple
                    # requests arrive at identical time.time() (sorted-set
                    # members are unique — same member == no new entry).
                    member = f"{now}:{uuid.uuid4().hex[:8]}"
                    add_pipe = self.redis.pipeline()
                    add_pipe.zadd(key, {member: now})
                    add_pipe.expire(key, limit.window_seconds + 1)
                    await add_pipe.execute()
            except Exception as e:
                log.warning("rate_limit.redis_error", error=str(e))
                # Fall through to in-memory.
                allowed = self._fallback_check(key, now, window_start, limit.max_requests)
        else:
            allowed = self._fallback_check(key, now, window_start, limit.max_requests)

        if not allowed:
            rate_limit_rejections_total.labels(bucket=limit.bucket).inc()
            # Don't bind to a single name — mypy narrows on first assignment,
            # which would reject the else-branch widening.
            if limit.auth_bucket:
                raise AuthRateLimited(
                    f"Too many attempts. Please wait {retry_after} seconds.",
                    details={"retry_after_seconds": retry_after},
                )
            raise RateLimited(
                f"Rate limit exceeded. Please wait {retry_after} seconds.",
                details={"retry_after_seconds": retry_after},
            )

    def _fallback_check(self, key: str, now: float, window_start: float, cap: int) -> bool:
        buf = self._fallback[key]
        # Purge.
        while buf and buf[0] < window_start:
            buf.pop(0)
        # Count BEFORE appending so rejected requests don't inflate the buffer.
        if len(buf) >= cap:
            return False
        buf.append(now)
        return True
