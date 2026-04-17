"""Redis-backed response cache for summary tables.

Shared across replicas so table builds are paid once per dataset/class/auth
tuple per TTL window. Plain JSON blob under a single key, no partitioning,
no eviction beyond TTL — tables are kilobytes, and the working set is small
(datasets × ~6 class views × 2 auth modes).

Plan §M4a step 3: "Redis-backed table cache is mandatory". Keys follow
`table:{datasetId}:{className}:{authed|public}` with a 1-hour TTL. Cache
misses fall through to the cloud builder; cache fills happen post-success
so a cloud failure never populates a stale entry.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from ..observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_TTL_SECONDS = 60 * 60  # 1 hour


class RedisTableCache:
    """get_or_compute over a Redis JSON blob, with per-key async locks to
    prevent thundering-herd re-computation when multiple requests miss the
    same key concurrently within one process.

    Cross-replica stampede is tolerable for tables: the cost of two replicas
    building the same table once is bounded and well under the cost of
    adding a distributed lock for the happy-path read.
    """

    def __init__(self, redis: Redis, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @staticmethod
    def table_key(dataset_id: str, class_name: str, *, authed: bool) -> str:
        mode = "authed" if authed else "public"
        return f"table:{dataset_id}:{class_name}:{mode}"

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(key, asyncio.Lock())

    async def get_or_compute(
        self,
        key: str,
        producer: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Read-through cache. Returns the cached JSON on hit; otherwise runs
        producer under a per-key lock, writes the result with TTL, and returns
        it. Producer errors propagate unchanged — nothing is written on error.
        """
        try:
            raw = await self.redis.get(key)
        except Exception as e:  # redis unavailable — degrade gracefully
            log.warning("table_cache.get_failed", key=key, error=str(e))
            raw = None
        if raw is not None:
            try:
                value = json.loads(raw)
                log.debug("table_cache.hit", key=key)
                return value
            except json.JSONDecodeError as e:
                log.warning("table_cache.corrupt_entry", key=key, error=str(e))
                # fall through and recompute; the corrupt blob will be overwritten

        lock = await self._get_lock(key)
        async with lock:
            # Double-check under lock — another coroutine may have just filled.
            try:
                raw = await self.redis.get(key)
                if raw is not None:
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        pass
            except Exception:  # noqa: S110 — same degrade-gracefully policy
                pass

            value = await producer()
            try:
                await self.redis.set(key, json.dumps(value), ex=self.ttl_seconds)
                log.debug("table_cache.fill", key=key, ttl=self.ttl_seconds)
            except Exception as e:
                log.warning("table_cache.set_failed", key=key, error=str(e))
            return value

    async def invalidate(self, key: str) -> None:
        try:
            await self.redis.delete(key)
        except Exception as e:
            log.warning("table_cache.invalidate_failed", key=key, error=str(e))
