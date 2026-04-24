"""Cancel-on-disconnect helper for long-running routes.

Audit 2026-04-23 (#62): endpoints like ``/datasets/:id/summary``,
``/datasets/:id/provenance``, ``/tables/combined``, and the grain pivot
can take >10s on cache miss. Before this helper, the service kept
pulling bulk-fetches and ndiquery resolutions after the client navigated
away — wasting Lambda concurrency on a response nobody would read. On
large provenance builds (``_MAX_UNIQUE_TARGETS=1000``) that was hundreds
of upstream invocations.

Approach: race the service coroutine against ``request.is_disconnected()``
polling. If the client hangs up before the service finishes, cancel the
service task. The propagated ``asyncio.CancelledError`` unwinds any
in-flight httpx calls (httpx respects task cancellation), so the cloud
stops getting hammered.

Poll interval: 1 second. Granular enough to catch disconnects quickly
without adding perceptible scheduling overhead.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from fastapi import Request

from ..observability.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

_POLL_INTERVAL_SECONDS = 1.0


async def cancel_on_disconnect(request: Request, coro: Awaitable[T]) -> T:
    """Await ``coro``; if ``request`` disconnects first, cancel the
    inner task and re-raise ``asyncio.CancelledError``.

    Callers should place this wrapper around service calls that may
    take >1s on cache miss. Fast-hit paths (cache warm) return before
    the first poll tick and pay no extra overhead.
    """
    task = asyncio.ensure_future(coro)
    try:
        while True:
            done, _pending = await asyncio.wait(
                {task},
                timeout=_POLL_INTERVAL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return task.result()
            if await request.is_disconnected():
                log.info(
                    "route.client_disconnected",
                    path=request.url.path,
                )
                task.cancel()
                # Await the cancellation to unwind cleanly before
                # propagating. CancelledError is what we want to surface.
                try:
                    await task
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    # If the task raised something else between the
                    # cancel signal and actual cancellation, log and
                    # surface the cancellation anyway — the client is
                    # gone, and we shouldn't pretend the request
                    # succeeded.
                    log.debug(
                        "route.cancel_race",
                        error=type(e).__name__,
                        path=request.url.path,
                    )
                    raise asyncio.CancelledError() from e
                raise asyncio.CancelledError()
    finally:
        # Defensive: if we're being torn down for any reason and the
        # inner task is still alive, cancel it to release cloud calls.
        if not task.done():
            task.cancel()
