"""DocumentService.list_by_class — anonymous inline-fallback path.

Pre-fix repro (production smoke 2026-04-26): cloud's `POST /ndiquery`
returns `number_matches: 0` for anonymous callers on published
datasets that DO have documents. The `GET /datasets/:id` response
carries the document-id array inline and works anonymously, so we
fall back to slicing that array when ndiquery comes up empty.

A follow-up smoke (same day) surfaced that some user-published datasets
(e.g. ``69bc5ca11d547b1f6d083761``) ALSO have an empty inline
``dataset.documents[]`` array. The third tier — ``GET /datasets/:id/
documents?page&pageSize`` (Mongo-backed, paginated) — handles that.

These tests pin:
  - ndiquery-empty + no-class-filter → falls back to inline IDs
  - ndiquery-empty + class-filter set → no fallback (returns 0)
  - ndiquery non-empty → no fallback (preserves authenticated path)
  - Inline pagination correct (page 2 of size 50 = ids[50:100])
  - inline-also-empty → third-tier Mongo fallback fires
  - third-tier returns nothing → service returns clean empty page
  - third-tier with class-filter set → no fallback (returns 0)
  - third-tier total comes from /document-count
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.document_service import DocumentService

DATASET_ID = "682e7772cdf3f24938176fac"
USER_PUBLISHED_DATASET_ID = "69bc5ca11d547b1f6d083761"


def _stub_cloud() -> Any:
    class _Stub:
        ndiquery = AsyncMock()
        get_dataset = AsyncMock()
        bulk_fetch = AsyncMock()
        list_documents_by_dataset = AsyncMock(return_value=[])
        get_dataset_document_count = AsyncMock(return_value=0)

    return _Stub()


@pytest.mark.asyncio
async def test_anon_unfiltered_falls_back_to_inline_ids() -> None:
    """ndiquery returns 0 (anon repro) + no class filter →
    fall back to dataset.documents inline ids, slice page-1."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {
        "documents": [], "number_matches": 0,
    }
    inline_ids = [f"id-{i:03d}" for i in range(120)]
    cloud.get_dataset.return_value = {
        "_id": DATASET_ID,
        "documents": inline_ids,
    }
    cloud.bulk_fetch.return_value = [
        {"id": x, "className": "session"} for x in inline_ids[0:50]
    ]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 120  # full inline-array length
    assert result["page"] == 1
    assert result["pageSize"] == 50
    assert len(result["documents"]) == 50
    cloud.get_dataset.assert_awaited_once_with(
        DATASET_ID, access_token=None,
    )
    cloud.bulk_fetch.assert_awaited_once_with(
        DATASET_ID, inline_ids[0:50], access_token=None,
    )


@pytest.mark.asyncio
async def test_inline_fallback_paginates_correctly() -> None:
    """Page 2 of size 50 should slice ids[50:100]."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    inline_ids = [f"id-{i:03d}" for i in range(120)]
    cloud.get_dataset.return_value = {"documents": inline_ids}
    cloud.bulk_fetch.return_value = [
        {"id": x} for x in inline_ids[50:100]
    ]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name=None,
        page=2,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 120
    cloud.bulk_fetch.assert_awaited_once_with(
        DATASET_ID, inline_ids[50:100], access_token=None,
    )


@pytest.mark.asyncio
async def test_class_filter_no_fallback_even_when_ndiquery_empty() -> None:
    """When the caller asks for `?class=session`, the inline-id array
    can't be filtered by class without a bulk-fetch + post-filter pass
    (expensive at scale). Stay on ndiquery's empty result rather than
    falling back."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name="session",
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 0
    assert result["documents"] == []
    cloud.get_dataset.assert_not_awaited()
    cloud.bulk_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_ndiquery_with_results_skips_fallback() -> None:
    """Authenticated case: ndiquery returns rows → use them, don't
    bother with the inline fallback path."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {
        "documents": [{"id": "id-001"}, {"id": "id-002"}],
        "number_matches": 2,
    }
    cloud.bulk_fetch.return_value = [
        {"id": "id-001"}, {"id": "id-002"},
    ]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token="bearer-token",
    )

    assert result["total"] == 2
    assert len(result["documents"]) == 2
    cloud.get_dataset.assert_not_awaited()  # fallback path NOT taken


@pytest.mark.asyncio
async def test_inline_fallback_handles_get_dataset_failure() -> None:
    """If get_dataset throws (network blip, 5xx), fall back to empty
    rather than 500'ing the documents endpoint."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    cloud.get_dataset.side_effect = RuntimeError("railway timeout")

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 0
    assert result["documents"] == []
    cloud.bulk_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_fallback_filters_non_string_entries() -> None:
    """Defensive: cloud occasionally ships full-doc-object entries in
    the inline `documents` array (older response shapes). Skip non-string
    entries cleanly so we don't bulk-fetch garbage IDs."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    cloud.get_dataset.return_value = {
        "documents": ["id-001", {"id": "id-broken"}, "id-002", None, "id-003"],
    }
    cloud.bulk_fetch.return_value = [
        {"id": "id-001"}, {"id": "id-002"}, {"id": "id-003"},
    ]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 3  # only the 3 string entries counted
    cloud.bulk_fetch.assert_awaited_once_with(
        DATASET_ID, ["id-001", "id-002", "id-003"], access_token=None,
    )


# ---------------------------------------------------------------------------
# Third-tier Mongo fallback (2026-04-26 follow-up)
#
# Repro: dataset 69bc5ca11d547b1f6d083761 has documentCount: 66533 via
# /document-class-counts, but `dataset.documents[]` is `[]` AND ndiquery
# anonymously returns `number_matches: 0`. Both PR #96 paths return 0.
# Third tier asks Mongo via the paginated documents-by-dataset route.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_third_tier_mongo_fallback_fires_when_inline_also_empty() -> None:
    """ndiquery=0 AND inline=empty → Mongo-backed
    `GET /datasets/:id/documents?page&pageSize` is called. Total comes
    from the dedicated `/document-count` endpoint."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    # Inline `documents[]` is empty (the user-published-dataset failure mode).
    cloud.get_dataset.return_value = {
        "_id": USER_PUBLISHED_DATASET_ID,
        "documents": [],
    }
    mongo_page_ids = [f"id-{i:05d}" for i in range(50)]
    cloud.list_documents_by_dataset.return_value = mongo_page_ids
    cloud.get_dataset_document_count.return_value = 66533
    cloud.bulk_fetch.return_value = [
        {"id": x, "className": "session"} for x in mongo_page_ids
    ]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=USER_PUBLISHED_DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 66533
    assert result["page"] == 1
    assert result["pageSize"] == 50
    assert len(result["documents"]) == 50
    cloud.list_documents_by_dataset.assert_awaited_once_with(
        USER_PUBLISHED_DATASET_ID,
        page=1,
        page_size=50,
        access_token=None,
    )
    cloud.get_dataset_document_count.assert_awaited_once_with(
        USER_PUBLISHED_DATASET_ID, access_token=None,
    )
    cloud.bulk_fetch.assert_awaited_once_with(
        USER_PUBLISHED_DATASET_ID, mongo_page_ids, access_token=None,
    )


@pytest.mark.asyncio
async def test_third_tier_returns_empty_when_mongo_route_also_empty() -> None:
    """Truly empty dataset: every fallback returns 0. Service returns a
    clean empty page rather than 500'ing or hitting bulk-fetch with [].
    """
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    cloud.get_dataset.return_value = {"documents": []}
    cloud.list_documents_by_dataset.return_value = []
    cloud.get_dataset_document_count.return_value = 0

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=USER_PUBLISHED_DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 0
    assert result["documents"] == []
    cloud.list_documents_by_dataset.assert_awaited_once()
    # No bulk-fetch when there are no ids to fetch.
    cloud.bulk_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_third_tier_skipped_when_class_filter_set() -> None:
    """Class-filtered requests must NOT fall through to the third tier
    either. Same rationale as the inline-id fallback: the Mongo route
    doesn't filter by class server-side and we don't want to post-filter
    a 100-page bulk-fetch loop on every class-filtered miss."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=USER_PUBLISHED_DATASET_ID,
        class_name="session",
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 0
    assert result["documents"] == []
    cloud.list_documents_by_dataset.assert_not_awaited()
    cloud.get_dataset_document_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_third_tier_inconsistent_count_falls_back_to_page_length() -> None:
    """If the Mongo route returns a page of ids but `/document-count`
    inconsistently reports 0, trust the page we have so the page still
    renders. Better to show a small (correct) page than '0 of 0'."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    cloud.get_dataset.return_value = {"documents": []}
    page_ids = ["id-001", "id-002", "id-003"]
    cloud.list_documents_by_dataset.return_value = page_ids
    cloud.get_dataset_document_count.return_value = 0  # inconsistent
    cloud.bulk_fetch.return_value = [{"id": x} for x in page_ids]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=USER_PUBLISHED_DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    # total falls back to page length so the user sees the actual rows.
    assert result["total"] == 3
    assert len(result["documents"]) == 3


@pytest.mark.asyncio
async def test_third_tier_skipped_when_inline_fallback_succeeds() -> None:
    """Tier 2 (inline-id) succeeds → tier 3 (Mongo route) must NOT fire.
    Belt-and-suspenders: confirm no extra round-trip in the common case."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": [], "number_matches": 0}
    cloud.get_dataset.return_value = {
        "documents": [f"id-{i:03d}" for i in range(20)],
    }
    cloud.bulk_fetch.return_value = [
        {"id": f"id-{i:03d}"} for i in range(20)
    ]

    svc = DocumentService(cloud)
    result = await svc.list_by_class(
        dataset_id=DATASET_ID,
        class_name=None,
        page=1,
        page_size=50,
        access_token=None,
    )

    assert result["total"] == 20
    cloud.list_documents_by_dataset.assert_not_awaited()
    cloud.get_dataset_document_count.assert_not_awaited()
