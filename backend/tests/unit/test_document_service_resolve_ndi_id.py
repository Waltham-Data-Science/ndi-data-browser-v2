"""Unit tests for DocumentService.detail() ndiId → Mongo `_id` resolution.

Covers the new branch added in `feat/triage-fixes-and-mockups`: when the
caller passes an ndiId (`<16hex>_<16hex>`) instead of a 24-char Mongo
ObjectId, DocumentService._resolve_ndi_id() falls back to an ndiquery
``exact_string base.id=<nid>`` and uses the first match's Mongo `_id`
to call ``get_document`` with.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.errors import NotFound
from backend.services.document_service import DocumentService

MONGO_ID = "68839b1fbf243809c0800a01"
NDI_ID = "41268d7e00bcb12b_40d0409f7d87ad23"
DATASET_ID = "68839b1fbf243809c0800a00"


def _stub_cloud() -> Any:
    """Minimal NdiCloudClient stand-in: async mocks for ndiquery +
    get_document. Tests set return values per case."""

    class _Stub:
        ndiquery = AsyncMock()
        get_document = AsyncMock()

    return _Stub()


@pytest.mark.asyncio
async def test_detail_with_mongo_id_skips_ndi_resolve() -> None:
    """Mongo `_id` should go straight to get_document — no extra ndiquery
    round-trip, no wasted Lambda call."""
    cloud = _stub_cloud()
    cloud.get_document.return_value = {
        "id": MONGO_ID,
        "ndiId": "x_y",
        "name": "Doc",
        "data": {"base": {"id": "x_y"}},
    }
    svc = DocumentService(cloud)

    got = await svc.detail(DATASET_ID, MONGO_ID, access_token=None)

    assert got["id"] == MONGO_ID
    cloud.get_document.assert_awaited_once_with(DATASET_ID, MONGO_ID, access_token=None)
    cloud.ndiquery.assert_not_called()


@pytest.mark.asyncio
async def test_detail_with_ndi_id_resolves_then_fetches() -> None:
    """ndiId triggers ndiquery → _id lookup, then get_document with the
    resolved Mongo `_id`."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {
        "documents": [{"id": MONGO_ID, "ndiId": NDI_ID}],
    }
    cloud.get_document.return_value = {
        "id": MONGO_ID,
        "ndiId": NDI_ID,
        "data": {"base": {"id": NDI_ID}},
    }
    svc = DocumentService(cloud)

    got = await svc.detail(DATASET_ID, NDI_ID, access_token="tok")

    assert got["id"] == MONGO_ID
    # Correct upstream calls — ndiquery scoped to the dataset, then
    # get_document with the Mongo `_id` (not the original ndiId).
    cloud.ndiquery.assert_awaited_once()
    q_kwargs = cloud.ndiquery.await_args.kwargs
    assert q_kwargs["scope"] == DATASET_ID
    assert q_kwargs["access_token"] == "tok"
    assert q_kwargs["searchstructure"] == [
        {"operation": "exact_string", "field": "base.id", "param1": NDI_ID},
    ]
    cloud.get_document.assert_awaited_once_with(DATASET_ID, MONGO_ID, access_token="tok")


@pytest.mark.asyncio
async def test_detail_ndi_id_no_match_raises_notfound() -> None:
    """ndiquery returns `{documents: []}` → clean NotFound, not 500."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {"documents": []}
    svc = DocumentService(cloud)

    with pytest.raises(NotFound) as exc:
        await svc.detail(DATASET_ID, NDI_ID, access_token=None)
    # BrowserError subclasses surface the caller-supplied message via
    # Exception.__str__ (see errors.BrowserError.__init__).
    assert NDI_ID in str(exc.value)
    cloud.get_document.assert_not_called()


@pytest.mark.asyncio
async def test_detail_ndi_id_resolved_doc_missing_id_raises_notfound() -> None:
    """Defensive: cloud returned a doc but neither `id` nor `_id` is set —
    surface as NotFound rather than forwarding None to get_document."""
    cloud = _stub_cloud()
    cloud.ndiquery.return_value = {
        "documents": [{"ndiId": NDI_ID, "name": "Orphan"}],  # no id/_id
    }
    svc = DocumentService(cloud)

    with pytest.raises(NotFound):
        await svc.detail(DATASET_ID, NDI_ID, access_token=None)
    cloud.get_document.assert_not_called()


@pytest.mark.asyncio
async def test_detail_ndi_id_prefers_exact_match_over_first_hit() -> None:
    """When ndiquery returns multiple docs (indexer anomaly), pick the
    one whose ndiId / base.id actually equals the query — not the first
    row blindly."""
    cloud = _stub_cloud()
    wrong_id = "ffffffffffffffffffffffff"
    right_id = MONGO_ID
    # First doc is a near-match (substring alias); second is the real one.
    cloud.ndiquery.return_value = {
        "documents": [
            {"id": wrong_id, "ndiId": "other_aliased"},
            {"id": right_id, "ndiId": NDI_ID},
        ],
    }
    cloud.get_document.return_value = {
        "id": right_id,
        "ndiId": NDI_ID,
        "data": {"base": {"id": NDI_ID}},
    }
    svc = DocumentService(cloud)

    await svc.detail(DATASET_ID, NDI_ID, access_token=None)
    cloud.get_document.assert_awaited_once_with(DATASET_ID, right_id, access_token=None)


@pytest.mark.asyncio
async def test_detail_ndi_id_bubbles_up_cloud_errors() -> None:
    """An ndiquery failure should propagate as-is — callers distinguish
    CloudTimeout / CloudUnreachable / CloudInternalError downstream. We
    must NOT swallow it as NotFound, which would tell users the ndiId
    doesn't exist when in fact the upstream failed."""
    cloud = _stub_cloud()
    cloud.ndiquery.side_effect = RuntimeError("boom")
    svc = DocumentService(cloud)

    with pytest.raises(RuntimeError):
        await svc.detail(DATASET_ID, NDI_ID, access_token=None)
    cloud.get_document.assert_not_called()
