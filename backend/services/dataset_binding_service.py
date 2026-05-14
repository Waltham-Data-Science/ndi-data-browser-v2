"""dataset_binding_service — Sprint 1.5 cloud-backed ``ndi.dataset.Dataset``
binding for the experimental ``/ask`` chat.

The chat already has a structured ``ndi_query`` tool that proxies to the
cloud-node Mongo layer. This service adds ONE more capability: surfacing
SDK-level abstractions (``dataset.elements()``, ``element.epochs()``,
session traversal) over a LOCAL copy of the dataset that NDI-python has
materialized via :func:`ndi.cloud.orchestration.downloadDataset`.

Why local materialization?
    Most useful summary numbers — element count, total epoch count
    across elements, list of (name, type) tuples — are computed by the
    SDK by walking ``element`` + ``element_epoch`` docs and traversing
    dependencies. The cloud-node's ``/ndiquery`` endpoint returns raw
    docs but doesn't perform that traversal. Spinning up a real
    ``ndi.dataset.Dataset`` once, in-process, lets us answer these
    "how many X are there?" questions cheaply.

Lifecycle
─────────
- First call for a given dataset_id is a cold load (10-30s typical for
  the demo datasets). The cold path runs ``downloadDataset`` in a
  thread so it doesn't block the asyncio loop.
- Subsequent calls are warm hits: bounded by an in-memory LRU.
- Cache is keyed by dataset_id; eviction at MAX_CACHED_DATASETS.
- Concurrent calls for the SAME dataset coalesce on an
  :class:`asyncio.Lock` so we never download twice in parallel.

Cache target folder
───────────────────
- Env-var ``NDI_CACHE_DIR`` (default ``/tmp/ndi-cache``).
- Per-dataset subfolder under that root; downloadDataset itself appends
  the dataset_id, so the on-disk layout is
  ``<NDI_CACHE_DIR>/<dataset_id>/.ndi/…``.
- /tmp is ephemeral on Railway (no persistent volume requested for
  this task) — that's fine for the demo. Entries get rebuilt after a
  redeploy and the pre-warm tasks fan out automatically.

Failure posture
───────────────
- Every public method NEVER raises. On any internal failure they log
  a warning and return None. Callers (the FastAPI router) treat None
  as "binding unavailable" → 503 → frontend tool falls back to
  /ndiquery. Safety > completeness.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..observability.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables (module-level so tests can monkeypatch them)
# ---------------------------------------------------------------------------

# Max simultaneously-cached datasets. Each cached dataset holds:
#   - the Python ndi_dataset_dir object (~MB-scale heap),
#   - its on-disk .ndi store under NDI_CACHE_DIR/<id>/.
# 5 is enough to cover the 3 demo datasets + headroom for occasional
# user-driven calls without ballooning memory or disk.
MAX_CACHED_DATASETS = 5

# Per-cold-load wall-clock cap. downloadDataset is mostly I/O-bound
# (bulk Mongo fetch + S3 presign rewrites). 90s gives slow networks
# enough rope; longer than that and we'd rather surface the failure to
# the caller than hold the request handler open.
COLD_LOAD_TIMEOUT_SECONDS = 90.0

# Soft size budget for the on-disk cache. We log a warning when the
# cache exceeds this; we don't auto-prune because eviction-on-LRU
# already bounds growth in the steady state. The warning is the
# operator hint that something's leaking.
CACHE_DIR_SOFT_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB

# Max elements surfaced in the overview payload. The LLM token budget
# won't survive a 500-element table; the dataset-detail page already
# shows the full element list, the chat is the wrong place for it.
MAX_ELEMENTS_IN_OVERVIEW = 50


# ---------------------------------------------------------------------------
# Internal cache entry — keeps the dataset object + bookkeeping together
# ---------------------------------------------------------------------------


class _CacheEntry:
    """Single LRU slot. ``dataset`` is the NDI-python object; ``loaded_at``
    powers the ``cache_age_seconds`` field in the overview response.

    Mutable on purpose: ``DatasetBindingService`` rewrites ``loaded_at``
    on every warm hit so the LRU ordering reflects recency-of-use, not
    recency-of-load.
    """

    __slots__ = ("dataset", "first_loaded_at", "loaded_at")

    def __init__(self, dataset: Any) -> None:
        now = time.monotonic()
        self.dataset = dataset
        self.loaded_at = now
        self.first_loaded_at = now


class DatasetBindingService:
    """LRU-cached wrapper around :func:`ndi.cloud.orchestration.downloadDataset`.

    Public surface is two coroutines: :meth:`get_dataset` and
    :meth:`overview`. Both swallow all exceptions and return ``None``
    on any failure so the router can map that to a 503 and the chat
    falls through to its existing tools.
    """

    def __init__(self, *, cache_dir: str | None = None) -> None:
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        # Per-dataset locks coalesce concurrent get_dataset() calls so
        # two requests for the same id share a single download.
        self._locks: dict[str, asyncio.Lock] = {}
        # Global lock guards _locks dict mutation and LRU eviction.
        self._mutex = asyncio.Lock()
        self._cache_dir = Path(
            cache_dir or os.environ.get("NDI_CACHE_DIR", "/tmp/ndi-cache")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_dataset(self, dataset_id: str) -> Any | None:
        """Return the cached ndi.dataset.Dataset for ``dataset_id``.

        Cold path: downloads (in a worker thread) under
        ``<NDI_CACHE_DIR>/<dataset_id>/`` and caches the result.
        Warm path: returns the cached object instantly + updates LRU
        position.

        Returns ``None`` on any failure (NDI-python unavailable,
        download timeout, exception during construction, etc.) — never
        raises. The router translates None → 503; the frontend tool
        translates 503 → "binding still warming, try ndi_query".
        """
        if not dataset_id:
            return None

        async with self._mutex:
            existing = self._cache.get(dataset_id)
            if existing is not None:
                # LRU bump + warm-hit log.
                self._cache.move_to_end(dataset_id)
                existing.loaded_at = time.monotonic()
                log.info(
                    "dataset_binding.warm_hit",
                    dataset_id=dataset_id,
                    cache_size=len(self._cache),
                )
                return existing.dataset
            # No cache entry. Acquire/create the per-dataset lock.
            per_lock = self._locks.setdefault(dataset_id, asyncio.Lock())

        # Hold the per-dataset lock to deduplicate concurrent cold
        # loads. After acquiring, re-check the cache — another caller
        # may have populated it while we waited.
        async with per_lock:
            async with self._mutex:
                existing = self._cache.get(dataset_id)
                if existing is not None:
                    self._cache.move_to_end(dataset_id)
                    log.info(
                        "dataset_binding.warm_hit_after_wait",
                        dataset_id=dataset_id,
                    )
                    return existing.dataset

            dataset = await self._cold_load(dataset_id)
            if dataset is None:
                return None

            async with self._mutex:
                self._cache[dataset_id] = _CacheEntry(dataset)
                self._cache.move_to_end(dataset_id)
                self._evict_lru_if_needed()
            return dataset

    async def overview(self, dataset_id: str) -> dict[str, Any] | None:
        """High-level summary: element / subject / epoch counts + element
        listing. See module docstring for why this matters.

        Returns ``None`` if the binding is unavailable. Callers route
        that to a 503.
        """
        if not dataset_id:
            return None

        # cache_hit reflects whether get_dataset hit a warm slot.
        # Capture BEFORE the call so we can tell cold from warm.
        async with self._mutex:
            had_entry = dataset_id in self._cache

        dataset = await self.get_dataset(dataset_id)
        if dataset is None:
            return None

        # Pull cache age (now in seconds) — after get_dataset() the
        # entry's loaded_at was bumped, so first_loaded_at gives us
        # the actual age since cold-load.
        async with self._mutex:
            entry = self._cache.get(dataset_id)
            cache_age_seconds = (
                time.monotonic() - entry.first_loaded_at if entry else 0.0
            )

        # Compute the actual overview off the event loop. Most of the
        # work is pure-Python iteration over the in-memory database,
        # but element.epochtable() may trigger file I/O for ingested
        # epochs. Threadpool it to be safe.
        try:
            payload: dict[str, Any] | None = await asyncio.to_thread(
                self._compute_overview, dataset
            )
        except Exception as exc:  # blind — overview must never raise
            log.warning(
                "dataset_binding.overview_failed",
                dataset_id=dataset_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        if payload is None:
            return None

        payload["cache_hit"] = had_entry
        payload["cache_age_seconds"] = round(cache_age_seconds, 2)
        return payload

    # ------------------------------------------------------------------
    # Cold-load + overview computation (run off the event loop)
    # ------------------------------------------------------------------

    async def _cold_load(self, dataset_id: str) -> Any | None:
        """Run ``downloadDataset`` in a worker thread with a wall-clock cap."""
        from . import ndi_python_service

        if not ndi_python_service.is_ndi_available():
            log.warning(
                "dataset_binding.ndi_unavailable",
                dataset_id=dataset_id,
            )
            return None

        # Ensure the cache root exists before handing it to
        # downloadDataset (which mkdirs its own per-dataset subfolder
        # but assumes the parent is writable).
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning(
                "dataset_binding.cache_dir_unwritable",
                cache_dir=str(self._cache_dir),
                error=str(exc),
            )
            return None

        log.info(
            "dataset_binding.cold_load_start",
            dataset_id=dataset_id,
            cache_dir=str(self._cache_dir),
        )
        start = time.monotonic()
        try:
            dataset = await asyncio.wait_for(
                asyncio.to_thread(self._download_blocking, dataset_id),
                timeout=COLD_LOAD_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            log.warning(
                "dataset_binding.cold_load_timeout",
                dataset_id=dataset_id,
                timeout_seconds=COLD_LOAD_TIMEOUT_SECONDS,
            )
            return None
        except Exception as exc:  # blind — cold load must never raise
            log.warning(
                "dataset_binding.cold_load_failed",
                dataset_id=dataset_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        duration_seconds = time.monotonic() - start
        log.info(
            "dataset_binding.cold_load",
            dataset_id=dataset_id,
            duration_seconds=round(duration_seconds, 2),
        )

        # Best-effort: warn if the on-disk cache has grown past the
        # soft budget. Computed lazily so we don't du -sh on every
        # call; cheap enough at cold-load granularity.
        self._warn_if_cache_oversized()
        return dataset

    def _download_blocking(self, dataset_id: str) -> Any:
        """Synchronous downloadDataset call. Lives in a thread.

        Lazy-import ndi.cloud here so this module stays cheap to import
        even when NDI-python isn't installed (test/CI matrix).
        """
        from ndi.cloud.orchestration import downloadDataset
        return downloadDataset(
            dataset_id,
            str(self._cache_dir),
            sync_files=False,
        )

    def _compute_overview(self, dataset: Any) -> dict[str, Any] | None:
        """Walk the dataset and return the LLM-facing summary dict.

        Runs on a worker thread. Tolerant of partial failures: each
        sub-count is wrapped in its own try/except so one missing
        traversal doesn't blank the whole payload.
        """
        # ------ element listing + count ------
        elements: list[Any] = []
        element_count = 0
        element_listing: list[dict[str, str]] = []
        try:
            session = getattr(dataset, "_session", None)
            if session is not None and hasattr(session, "getelements"):
                elements = list(session.getelements()) or []
                element_count = len(elements)
                for elem in elements[:MAX_ELEMENTS_IN_OVERVIEW]:
                    name = str(getattr(elem, "name", "") or "")
                    etype = str(getattr(elem, "type", "") or "")
                    if name or etype:
                        element_listing.append({"name": name, "type": etype})
        except Exception as exc:
            log.warning(
                "dataset_binding.element_listing_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            elements = []
            element_count = 0
            element_listing = []

        # ------ subject count via isa('subject') search ------
        # The ndi_query import is inside the try so a missing SDK
        # version on a dev machine downgrades subject_count to 0
        # without blanking the rest of the payload.
        subject_count = 0
        try:
            from ndi.query import ndi_query
            subj_docs = dataset.database_search(
                ndi_query("").isa("subject")
            )
            subject_count = len(subj_docs) if subj_docs is not None else 0
        except Exception as exc:
            log.warning(
                "dataset_binding.subject_count_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # ------ epoch count via per-element epochtable ------
        # We sum across ALL elements (not just the first
        # MAX_ELEMENTS_IN_OVERVIEW) to preserve count fidelity. Each
        # element's numepochs() walks the in-memory epoch table; the
        # cost is bounded by element count * avg epochs.
        epoch_count = 0
        try:
            for elem in elements:
                try:
                    if hasattr(elem, "numepochs"):
                        epoch_count += int(elem.numepochs())
                    else:
                        et, _ = elem.epochtable()
                        epoch_count += len(et) if et else 0
                except Exception as exc:
                    # Per-element failure: log but keep counting.
                    log.debug(
                        "dataset_binding.element_epoch_count_failed",
                        element_name=str(getattr(elem, "name", "")),
                        error=str(exc),
                    )
        except Exception as exc:
            log.warning(
                "dataset_binding.epoch_count_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # ------ dataset reference (for citation snippet) ------
        reference = ""
        try:
            reference = str(getattr(dataset, "reference", "") or "")
        except Exception:
            reference = ""

        return {
            "element_count": element_count,
            "subject_count": subject_count,
            "epoch_count": epoch_count,
            "elements": element_listing,
            "elements_truncated": element_count > len(element_listing),
            "reference": reference,
        }

    # ------------------------------------------------------------------
    # LRU eviction + disk-usage guard
    # ------------------------------------------------------------------

    def _evict_lru_if_needed(self) -> None:
        """Drop the least-recently-used entry when the cache is full.

        Called under self._mutex. We don't unlink the on-disk folder
        of the evicted dataset — leaving it lets a later cold-load
        skip the network entirely (downloadDataset reuses an existing
        target folder if the JSONs are already there).
        """
        while len(self._cache) > MAX_CACHED_DATASETS:
            oldest_id, _ = self._cache.popitem(last=False)
            self._locks.pop(oldest_id, None)
            log.info(
                "dataset_binding.evicted",
                dataset_id=oldest_id,
                cache_size=len(self._cache),
            )

    def _warn_if_cache_oversized(self) -> None:
        """Best-effort disk-usage check. Walks the cache dir once per
        cold load; cheap relative to a download but still bounded.
        """
        try:
            if not self._cache_dir.exists():
                return
            total = 0
            for path in self._cache_dir.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
            if total > CACHE_DIR_SOFT_LIMIT_BYTES:
                log.warning(
                    "dataset_binding.cache_dir_oversized",
                    cache_dir=str(self._cache_dir),
                    size_bytes=total,
                    limit_bytes=CACHE_DIR_SOFT_LIMIT_BYTES,
                )
        except Exception as exc:
            log.debug(
                "dataset_binding.cache_dir_size_check_failed",
                error=str(exc),
            )
