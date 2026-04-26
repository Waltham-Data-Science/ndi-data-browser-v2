"""DocumentService.list_by_class — anonymous inline-fallback path.

Pre-fix repro (production smoke 2026-04-26): cloud's `POST /ndiquery`
returns `number_matches: 0` for anonymous callers on published
datasets that DO have documents. The `GET /datasets/:id` response
carries the document-id array inline and works anonymously, so we
fall back to slicing that array when ndiquery comes up empty.

These tests pin:
  - ndiquery-empty + no-class-filter → falls back to inline IDs
  - ndiquery-empty + class-filter set → no fallback (returns 0)
  - ndiquery non-empty → no fallback (preserves authenticated path)
  - Inline pagination correct (page 2 of size 50 = ids[50:100])
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.document_service import DocumentService

DATASET_ID = "682e7772cdf3f24938176fac"


def _stub_cloud() -> Any:
    class _Stub:
        ndiquery = AsyncMock()
        get_dataset = AsyncMock()
        bulk_fetch = AsyncMock()

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
