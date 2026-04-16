"""In-process TTL cache abstraction.

Tiny wrapper around cachetools.TTLCache + async locks to prevent thundering-herd
recomputation when a cache entry is missed under concurrent load.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from cachetools import TTLCache

T = TypeVar("T")


class AsyncTTLCache(Generic[T]):
    def __init__(self, maxsize: int, ttl_seconds: float) -> None:
        self._cache: TTLCache[str, T] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_for_locks = asyncio.Lock()

    async def get_or_compute(self, key: str, producer: Callable[[], Awaitable[T]]) -> T:
        if key in self._cache:
            return self._cache[key]
        # Per-key lock to avoid stampede.
        async with self._lock_for_locks:
            lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._cache:
                return self._cache[key]
            value = await producer()
            self._cache[key] = value
            return value

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def currsize(self) -> int:
        return self._cache.currsize


# Pre-configured caches used across services.
class ProxyCaches:
    class_counts: AsyncTTLCache[Any] = AsyncTTLCache(maxsize=1024, ttl_seconds=300)
    datasets_list: AsyncTTLCache[Any] = AsyncTTLCache(maxsize=64, ttl_seconds=60)
    dataset_detail: AsyncTTLCache[Any] = AsyncTTLCache(maxsize=512, ttl_seconds=60)
