"""Server-side pagination on /tables/{class} (Stream 5.8, 2026-05-16).

Locks the new pagination contract:

* When neither ``page`` nor ``page_size`` is supplied, the response keeps
  the legacy unpaged envelope (BC for the Document Explorer + cron).
* When either is supplied, the response gains ``{page, pageSize, totalRows,
  hasMore}`` and ``rows`` is sliced server-side.
* Pagination happens AFTER the cache layer so the cache stays keyed by
  ``(dataset_id, class_name, user_scope)`` only — every page reads from
  the same cached full envelope.

The unit test exercises the pure ``_paginate`` helper plus the
``single_class`` flow with a stubbed cloud client so the cache + slice
chain is end-to-end testable without a live Railway env.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services.summary_table_service import (
    SummaryTableService,
    _paginate,
)

# ---------------------------------------------------------------------------
# Pure helper: _paginate
# ---------------------------------------------------------------------------

def _envelope(n: int) -> dict[str, Any]:
    """Build a synthetic full-table envelope with ``n`` rows."""
    return {
        "columns": [{"key": "x", "label": "X"}],
        "rows": [{"x": i} for i in range(n)],
        "distinct_summary": {"x": {"distinct_count": n, "top_values": []}},
    }


class TestPaginateHelper:
    def test_first_page_with_more_to_come(self) -> None:
        out = _paginate(_envelope(500), page=1, page_size=200)
        assert out["page"] == 1
        assert out["pageSize"] == 200
        assert out["totalRows"] == 500
        assert out["hasMore"] is True
        assert len(out["rows"]) == 200
        # First row should be index 0; last index 199.
        assert out["rows"][0]["x"] == 0
        assert out["rows"][-1]["x"] == 199

    def test_middle_page(self) -> None:
        out = _paginate(_envelope(500), page=2, page_size=200)
        assert out["page"] == 2
        assert out["totalRows"] == 500
        assert out["hasMore"] is True
        assert len(out["rows"]) == 200
        assert out["rows"][0]["x"] == 200
        assert out["rows"][-1]["x"] == 399

    def test_last_page_partial(self) -> None:
        out = _paginate(_envelope(500), page=3, page_size=200)
        assert out["page"] == 3
        assert out["totalRows"] == 500
        # 500 rows / 200 page_size = page 3 has rows 400-499 (100 rows).
        assert out["hasMore"] is False
        assert len(out["rows"]) == 100
        assert out["rows"][0]["x"] == 400
        assert out["rows"][-1]["x"] == 499

    def test_page_beyond_total_yields_empty_rows(self) -> None:
        out = _paginate(_envelope(50), page=2, page_size=200)
        # Page 2 of a 50-row table is past the end. Don't error; return
        # an empty rows array so callers can still inspect totalRows +
        # hasMore.
        assert out["rows"] == []
        assert out["totalRows"] == 50
        assert out["hasMore"] is False

    def test_carries_distinct_summary_verbatim(self) -> None:
        full = _envelope(500)
        out = _paginate(full, page=1, page_size=10)
        # distinct_summary is full-table — should be unchanged regardless
        # of how the rows are sliced.
        assert out["distinct_summary"] == full["distinct_summary"]

    def test_carries_columns_verbatim(self) -> None:
        full = _envelope(50)
        out = _paginate(full, page=1, page_size=200)
        assert out["columns"] == full["columns"]

    def test_empty_full_table(self) -> None:
        out = _paginate({"columns": [], "rows": []}, page=1, page_size=200)
        assert out["rows"] == []
        assert out["totalRows"] == 0
        assert out["hasMore"] is False


# ---------------------------------------------------------------------------
# single_class flow — verify BC unpaged path + paged path
# ---------------------------------------------------------------------------

class _FakeCache:
    """In-memory cache that mimics RedisTableCache's get_or_compute API."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self.compute_count = 0

    async def get_or_compute(self, key: str, compute: Any) -> dict[str, Any]:
        if key in self._store:
            return self._store[key]
        self.compute_count += 1
        value = await compute()
        self._store[key] = value
        return value


class _StubService(SummaryTableService):
    """SummaryTableService that bypasses the real cloud + projection so
    pagination unit tests don't need fixture docs. ``_build_single_class``
    is mocked to return a fixed envelope.
    """

    def __init__(self, full_envelope: dict[str, Any]) -> None:
        self._envelope = full_envelope
        self.cache = _FakeCache()
        self.cloud = None  # type: ignore[assignment]

    async def _build_single_class(  # type: ignore[override]
        self,
        dataset_id: str,  # noqa: ARG002 — args required by parent signature; test ignores them
        class_name: str,  # noqa: ARG002
        *,
        access_token: str | None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return self._envelope


@pytest.mark.asyncio
async def test_single_class_unpaged_returns_full_envelope() -> None:
    """When page/page_size are both None the response keeps the legacy shape."""
    full = _envelope(300)
    svc = _StubService(full)

    result = await svc.single_class("DS1", "subject", session=None)

    assert "page" not in result
    assert "pageSize" not in result
    assert "totalRows" not in result
    assert "hasMore" not in result
    assert len(result["rows"]) == 300
    assert result["columns"] == full["columns"]


@pytest.mark.asyncio
async def test_single_class_paged_slices_server_side() -> None:
    """Passing page+page_size returns the paged envelope."""
    full = _envelope(750)
    svc = _StubService(full)

    page1 = await svc.single_class(
        "DS1", "subject", session=None, page=1, page_size=200,
    )
    assert page1["page"] == 1
    assert page1["pageSize"] == 200
    assert page1["totalRows"] == 750
    assert page1["hasMore"] is True
    assert len(page1["rows"]) == 200

    page2 = await svc.single_class(
        "DS1", "subject", session=None, page=2, page_size=200,
    )
    assert page2["page"] == 2
    assert page2["rows"][0]["x"] == 200


@pytest.mark.asyncio
async def test_pagination_shares_one_cached_full_envelope() -> None:
    """The cache is keyed by (dataset, class) — not by page. Asking for
    pages 1, 2, 3 should compute the full envelope ONCE; subsequent pages
    hit the cache and slice in-memory.

    This is THE egress-saving invariant: the cloud-fetch + projection work
    happens once per dataset/class regardless of how many pages a viewer
    requests."""
    full = _envelope(1000)
    svc = _StubService(full)

    # First request — populates cache.
    await svc.single_class(
        "DS1", "subject", session=None, page=1, page_size=200,
    )
    # Three more requests at different pages should all hit cache.
    await svc.single_class(
        "DS1", "subject", session=None, page=2, page_size=200,
    )
    await svc.single_class(
        "DS1", "subject", session=None, page=3, page_size=200,
    )
    # An unpaged request from a different consumer also hits the same cache.
    await svc.single_class("DS1", "subject", session=None)

    cache = svc.cache
    assert isinstance(cache, _FakeCache)
    assert cache.compute_count == 1


@pytest.mark.asyncio
async def test_single_class_only_page_defaults_page_size() -> None:
    """If only ``page`` is supplied, page_size defaults to 200."""
    full = _envelope(500)
    svc = _StubService(full)

    result = await svc.single_class(
        "DS1", "subject", session=None, page=1,
    )
    assert result["page"] == 1
    assert result["pageSize"] == 200
    assert len(result["rows"]) == 200


@pytest.mark.asyncio
async def test_single_class_only_page_size_defaults_page() -> None:
    """If only ``page_size`` is supplied, page defaults to 1."""
    full = _envelope(500)
    svc = _StubService(full)

    result = await svc.single_class(
        "DS1", "subject", session=None, page_size=100,
    )
    assert result["page"] == 1
    assert result["pageSize"] == 100
    assert len(result["rows"]) == 100
    assert result["rows"][0]["x"] == 0
