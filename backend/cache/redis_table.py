"""Redis-backed response cache for summary tables.

Shared across replicas so table builds are paid once per dataset/class/user
tuple per TTL window. Plain JSON blob under a single key, no partitioning,
no eviction beyond TTL — tables are kilobytes, and the working set is small
(datasets x ~6 class views x (N authenticated users + 1 public bucket)).

Plan §M4a step 3: "Redis-backed table cache is mandatory". Keys follow
`table:{version}:{datasetId}:{className}:{user_scope}` with a 1-hour TTL.
``user_scope`` is ``"public"`` for unauthenticated reads or
``"u:<sha256(user_id)[:16]>"`` for authenticated reads (see PR-3 and
:func:`backend.auth.session.user_scope_for`). Cache misses fall through
to the cloud builder; cache fills happen post-success so a cloud failure
never populates a stale entry.
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

    # Schema version for cached blobs. Bump whenever the response shape
    # changes or the projection's failure semantics change, so stale blobs
    # in Redis are invalidated by key-name change rather than waiting for
    # TTL. Cheaper than flushing Redis on every deploy.
    #
    # v1 = pre-M4a projection (6-col subject row, no ontology pairs).
    # v2 = M4a+ projection (15-col subject row, Schema-A/B dispatch,
    #      {devTime, globalTime} epoch objects, probe_location + treatment
    #      enrichment).
    # v3 = same projection as v2 but enrichment failures now raise instead
    #      of silently returning empty enrichment (which got cached). Fixes
    #      the Haley-subject-table empty-ontology blob observed post-M7 deploy.
    # v4: cache keys now include user_scope. See ADR / PR-3.
    #     Replaces the 1-bit ``authed: bool`` dimension with a stable per-user
    #     identifier so two authenticated users can never share a cached entry.
    #     Bumping the schema version ensures in-flight v3 entries are ignored
    #     post-deploy (they TTL out naturally within one hour).
    SCHEMA_VERSION = "v4"

    @staticmethod
    def table_key(dataset_id: str, class_name: str, *, user_scope: str) -> str:
        """Compose a Redis key for a summary-table blob.

        ``user_scope`` is an opaque cache scope: ``"public"`` for
        unauthenticated reads or ``"u:<16-hex>"`` for authenticated reads
        (see :func:`backend.auth.session.user_scope_for`). Must not contain
        ``:`` beyond the fixed ``u:`` prefix so the key structure stays
        parseable.
        """
        return (
            f"table:{RedisTableCache.SCHEMA_VERSION}:"
            f"{dataset_id}:{class_name}:{user_scope}"
        )

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(key, asyncio.Lock())

    async def get_or_compute(
        self,
        key: str,
        producer: Callable[[], Awaitable[dict[str, Any]]],
        *,
        ttl_for: Callable[[dict[str, Any]], int] | None = None,
    ) -> dict[str, Any]:
        """Read-through cache. Returns the cached JSON on hit; otherwise runs
        producer under a per-key lock, writes the result with TTL, and returns
        it. Producer errors propagate unchanged — nothing is written on error.

        ``ttl_for`` is an optional **per-call** TTL selector: a function that
        examines the just-computed value and returns the TTL (in seconds)
        the entry should live for. Falls back to ``self.ttl_seconds`` when
        unset, preserving backward-compatible behavior.

        # Why per-call TTL

        Some entries are "fully successful" (deserves long TTL — re-running
        the producer is expensive and the result is unlikely to change).
        Others are "degraded" (e.g. partial data because an upstream
        dependency timed out — deserves a SHORTER TTL so the next caller
        re-runs the producer sooner, giving us another chance to land a
        full result before viewers see staleness).

        Example: dataset_summary_service caches summaries for 24h on
        full success and 5 minutes on degraded results, so a frontend
        cron re-warming every 5 min has frequent retry chances on
        degraded entries while full successes ride out a whole day.

        Returning a 0 TTL via ``ttl_for`` skips the cache write
        altogether — useful when the producer's result is too partial
        to be worth caching at all.
        """
        try:
            raw = await self.redis.get(key)
        except Exception as e:  # redis unavailable — degrade gracefully
            log.warning("table_cache.get_failed", key=key, error=str(e))
            raw = None
        if raw is not None:
            try:
                # json.loads returns Any; callers (and this method) promise a
                # dict response shape.
                value: dict[str, Any] = json.loads(raw)
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
                        locked_value: dict[str, Any] = json.loads(raw)
                        return locked_value
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

            value = await producer()
            ttl = ttl_for(value) if ttl_for is not None else self.ttl_seconds
            if ttl > 0:
                try:
                    await self.redis.set(key, json.dumps(value), ex=ttl)
                    log.debug("table_cache.fill", key=key, ttl=ttl)
                except Exception as e:
                    log.warning("table_cache.set_failed", key=key, error=str(e))
            else:
                # ttl=0 means "don't cache" — used when the producer's
                # result is too partial to be worth holding at all.
                log.debug("table_cache.skip_write", key=key)
            return value

    async def invalidate(self, key: str) -> None:
        try:
            await self.redis.delete(key)
        except Exception as e:
            log.warning("table_cache.invalidate_failed", key=key, error=str(e))
