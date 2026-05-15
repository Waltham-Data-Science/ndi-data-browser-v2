"""Unit tests for SpikeSummaryService.

Mocks the cloud HTTP layer via respx (mirrors the pattern in
``test_pivot_service.py``). Exercises:

- ``unit_doc_id`` single-doc fetch path → returns one unit with
  spikes + ISIs.
- ``unit_name_match`` query path → returns N units in name order.
- Bare scan with N > ``max_units`` → returns capped slice with
  ``total_matching`` reflecting full count.
- Stride-sample cap: a doc with > MAX_SPIKES_PER_UNIT spikes returns
  the capped count.
- Empty: zero matching docs → ``units=[]`` and ``total_matching=0``
  with the empty-reason envelope populated.
- Soft error: matched doc with no parseable spike_times → unit entry
  with ``error_kind='decode_failed'`` instead of crashing.
- ``kind`` gating: ``raster`` omits ISIs, ``isi_histogram`` omits
  spike_times, ``both`` returns both.
- ``t_window`` filter trims spikes before stride-sampling.
"""
from __future__ import annotations

from typing import Any

import pytest
import respx
from cryptography.fernet import Fernet

from backend.clients.ndi_cloud import NdiCloudClient
from backend.services.document_service import DocumentService
from backend.services.spike_summary_service import (
    DEFAULT_MAX_UNITS,
    MAX_SPIKES_PER_UNIT,
    SpikeSummaryRequest,
    SpikeSummaryUnit,
    _build_isi_field,
    _build_spike_field,
    _extract_spike_times,
    _pick_doc_id,
    _pick_unit_name,
    _stride_sample,
    compute_spike_summary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def cloud() -> NdiCloudClient:  # type: ignore[no-untyped-def]
    """Shared cloud client. Mirrors test_pivot_service.cloud."""
    import os
    os.environ.setdefault("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = NdiCloudClient()
    await client.start()
    try:
        yield client
    finally:
        await client.close()


def _make_doc(
    doc_id: str,
    name: str,
    spike_times: list[float],
    *,
    key: str = "spike_times",
) -> dict[str, Any]:
    """Build a vmspikesummary document body in the shape the cloud
    returns from ndiquery / bulk-fetch.

    Defaults to ``data.vmspikesummary.spike_times`` (the most-common
    field path). ``key`` overrides it for tests probing the
    ``spiketimes`` / ``sample_times`` fallbacks.
    """
    return {
        "id": doc_id,
        "ndiId": f"ndi-{doc_id}",
        "name": name,
        "data": {
            "base": {"id": f"ndi-{doc_id}", "name": name},
            "vmspikesummary": {
                "name": name,
                key: spike_times,
            },
        },
    }


def _detail_body(doc: dict[str, Any]) -> dict[str, Any]:
    """The single-doc endpoint hoists the body to top-level (see
    ``DocumentService._normalize_document``). We mirror that here so
    the DocumentService's normalizer roundtrips into the bulk-fetch
    shape.
    """
    out: dict[str, Any] = {k: v for k, v in doc.items() if k != "data"}
    out.update(doc.get("data", {}))
    return out


def _ndiquery_body(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "number_matches": len(docs),
        "pageSize": 1000,
        "page": 1,
        "documents": docs,
    }


# ---------------------------------------------------------------------------
# Pure helper unit tests — no HTTP, no fixtures.
# ---------------------------------------------------------------------------


class TestExtractSpikeTimes:
    def test_canonical_spike_times_field(self) -> None:
        doc = _make_doc("d1", "Unit 1", [0.1, 0.2, 0.5])
        assert _extract_spike_times(doc) == [0.1, 0.2, 0.5]

    def test_spiketimes_fallback(self) -> None:
        doc = _make_doc("d1", "U", [1.0, 2.0], key="spiketimes")
        assert _extract_spike_times(doc) == [1.0, 2.0]

    def test_sample_times_canonical_schema_name(self) -> None:
        # Schema-canonical fallback. Used by older NDI versions.
        doc = _make_doc("d1", "U", [3.0], key="sample_times")
        assert _extract_spike_times(doc) == [3.0]

    def test_returns_none_when_no_data(self) -> None:
        assert _extract_spike_times({}) is None
        assert _extract_spike_times({"data": {}}) is None
        assert _extract_spike_times({"data": {"vmspikesummary": {}}}) is None

    def test_parses_stringified_numbers(self) -> None:
        # Some NDI exports stringify floats. Matches the TS handler.
        doc = {
            "data": {
                "vmspikesummary": {"spike_times": ["0.1", "0.2", "bogus", "0.5"]},
            },
        }
        assert _extract_spike_times(doc) == [0.1, 0.2, 0.5]

    def test_skips_non_finite_values(self) -> None:
        doc = {
            "data": {
                "vmspikesummary": {
                    "spike_times": [
                        0.1, float("nan"), 0.2, float("inf"), 0.3, float("-inf"),
                    ],
                },
            },
        }
        assert _extract_spike_times(doc) == [0.1, 0.2, 0.3]

    def test_empty_array_returns_none(self) -> None:
        doc = {"data": {"vmspikesummary": {"spike_times": []}}}
        assert _extract_spike_times(doc) is None


class TestPickIds:
    def test_pick_doc_id_prefers_id(self) -> None:
        assert _pick_doc_id({"id": "A", "_id": "B"}) == "A"
        assert _pick_doc_id({"_id": "B"}) == "B"
        assert _pick_doc_id({"ndiId": "C"}) == "C"
        assert _pick_doc_id({}) == ""

    def test_pick_unit_name_prefers_inner_name(self) -> None:
        doc = {
            "name": "outer",
            "data": {"vmspikesummary": {"name": "inner"}},
        }
        assert _pick_unit_name(doc, "did") == "inner"

    def test_pick_unit_name_falls_back_to_top_level(self) -> None:
        assert _pick_unit_name({"name": "outer"}, "did") == "outer"

    def test_pick_unit_name_falls_back_to_id_tail(self) -> None:
        assert _pick_unit_name({}, "abc1234567") == "Unit 234567"


class TestStrideSample:
    def test_under_cap_returns_all(self) -> None:
        assert _stride_sample([1.0, 2.0, 3.0], cap=10) == [1.0, 2.0, 3.0]

    def test_over_cap_preserves_endpoints(self) -> None:
        vals = [float(i) for i in range(1000)]
        out = _stride_sample(vals, cap=50)
        assert len(out) == 50
        assert out[0] == 0.0
        assert out[-1] == 999.0


class TestKindGating:
    def test_raster_kind_omits_isi(self) -> None:
        spikes = [0.0, 0.1, 0.2]
        assert _build_spike_field(spikes, "raster") == spikes
        assert _build_isi_field(spikes, "raster") is None

    def test_isi_histogram_kind_omits_spike_times(self) -> None:
        spikes = [0.0, 0.1, 0.2]
        assert _build_spike_field(spikes, "isi_histogram") is None
        intervals = _build_isi_field(spikes, "isi_histogram")
        assert intervals is not None
        # diff of [0, 0.1, 0.2] = [0.1, 0.1]; ms = [100, 100].
        assert intervals == pytest.approx([100.0, 100.0])

    def test_both_kind_returns_both(self) -> None:
        spikes = [0.0, 0.05]
        s = _build_spike_field(spikes, "both")
        isi = _build_isi_field(spikes, "both")
        assert s == spikes
        assert isi == pytest.approx([50.0])

    def test_isi_with_single_spike_returns_empty(self) -> None:
        # 1 spike → no intervals possible.
        assert _build_isi_field([0.5], "both") == []

    def test_isi_drops_zero_and_negative_intervals(self) -> None:
        # Duplicate timestamp would produce a 0-interval; we drop it.
        intervals = _build_isi_field([0.0, 0.0, 0.1], "both")
        assert intervals == pytest.approx([100.0])


# ---------------------------------------------------------------------------
# Service-level tests with respx-mocked cloud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unit_doc_id_single_doc_path(cloud: NdiCloudClient) -> None:
    """Direct fetch by unit_doc_id returns one unit."""
    # 24-char Mongo id so the DocumentService doesn't try to resolve it
    # via ndiquery first (that's a separate code path tested elsewhere).
    dataset_id = "DS_SPIKE_1"
    doc_id = "a" * 24
    doc = _make_doc(doc_id, "Unit Saline", [0.1, 0.2, 0.3, 0.4])
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}/documents/{doc_id}").respond(
            200, json=_detail_body(doc),
        )
        docs = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            unitDocId=doc_id,
            kind="both",
        )
        response = await compute_spike_summary(
            request,
            document_service=docs,
            cloud=cloud,
            session=None,
        )

    assert response.total_matching == 1
    assert response.kind == "both"
    assert len(response.units) == 1
    unit = response.units[0]
    assert unit.name == "Unit Saline"
    assert unit.doc_id == doc_id
    assert unit.spike_times == [0.1, 0.2, 0.3, 0.4]
    # ISIs in ms: diff of [0.1, 0.2, 0.3, 0.4] = [0.1, 0.1, 0.1] → [100, 100, 100].
    assert unit.isi_intervals == pytest.approx([100.0, 100.0, 100.0])
    assert unit.error is None


@pytest.mark.asyncio
async def test_unit_name_match_query_returns_ordered_units(
    cloud: NdiCloudClient,
) -> None:
    """Query path with substring filter returns N units sorted by name."""
    dataset_id = "DS_SPIKE_2"
    docs_in = [
        _make_doc("d1", "Unit 3 (Saline)", [0.0, 0.1]),
        _make_doc("d2", "Unit 1 (Saline)", [0.0, 0.2]),
        _make_doc("d3", "Unit 2 (Saline)", [0.0, 0.3]),
    ]
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        # ndiquery is auto-paginated by the cloud client; one page is enough.
        router.post("/ndiquery").respond(200, json=_ndiquery_body(docs_in))
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            unitNameMatch="Saline",
            kind="raster",
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert response.total_matching == 3
    assert len(response.units) == 3
    # Sorted by name → Unit 1 < Unit 2 < Unit 3.
    assert [u.name for u in response.units] == [
        "Unit 1 (Saline)",
        "Unit 2 (Saline)",
        "Unit 3 (Saline)",
    ]
    # kind='raster' → spike_times populated, isi_intervals omitted.
    for unit in response.units:
        assert unit.spike_times is not None
        assert unit.isi_intervals is None


@pytest.mark.asyncio
async def test_bare_scan_caps_at_max_units(cloud: NdiCloudClient) -> None:
    """Bare dataset scan honors max_units while surfacing total_matching."""
    dataset_id = "DS_SPIKE_3"
    # 15 docs but max_units=5 → response.units has 5 entries; total_matching=15.
    docs_in = [
        _make_doc(f"d{i}", f"Unit {i:02d}", [float(i), float(i) + 0.1])
        for i in range(15)
    ]
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/ndiquery").respond(200, json=_ndiquery_body(docs_in))
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            kind="both",
            maxUnits=5,
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert response.total_matching == 15
    assert len(response.units) == 5


@pytest.mark.asyncio
async def test_stride_sample_caps_high_spike_count_unit(
    cloud: NdiCloudClient,
) -> None:
    """A doc with > MAX_SPIKES_PER_UNIT spikes returns the capped count."""
    dataset_id = "DS_SPIKE_4"
    # 10_000 spikes → cap at MAX_SPIKES_PER_UNIT (5000).
    big_spikes = [i * 0.001 for i in range(10_000)]
    doc = _make_doc("a" * 24, "Big Unit", big_spikes)
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}/documents/{'a' * 24}").respond(
            200, json=_detail_body(doc),
        )
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            unitDocId="a" * 24,
            kind="raster",
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert len(response.units) == 1
    unit = response.units[0]
    assert unit.spike_times is not None
    assert len(unit.spike_times) <= MAX_SPIKES_PER_UNIT
    # First + last preserved by stride-sample.
    assert unit.spike_times[0] == pytest.approx(0.0)
    assert unit.spike_times[-1] == pytest.approx(9.999)


@pytest.mark.asyncio
async def test_empty_match_returns_empty_units_with_error_envelope(
    cloud: NdiCloudClient,
) -> None:
    """Zero matching docs → empty units + total_matching=0 + reason."""
    dataset_id = "DS_SPIKE_5"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/ndiquery").respond(200, json=_ndiquery_body([]))
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            unitNameMatch="NonexistentLabel",
            kind="both",
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert response.units == []
    assert response.total_matching == 0
    assert response.error_kind == "no_matches"
    assert response.error is not None
    assert "NonexistentLabel" in response.error


@pytest.mark.asyncio
async def test_decode_failure_yields_per_unit_soft_error(
    cloud: NdiCloudClient,
) -> None:
    """A matched doc with no parseable spike_times surfaces as a unit
    entry with error_kind='decode_failed' instead of crashing.
    """
    dataset_id = "DS_SPIKE_6"
    # Doc body where the vmspikesummary subtree exists but spike_times
    # is missing. Mirrors a malformed export the chat tool used to crash on.
    doc = {
        "id": "b" * 24,
        "ndiId": "ndi-b",
        "name": "Broken Unit",
        "data": {
            "base": {"id": "ndi-b", "name": "Broken Unit"},
            "vmspikesummary": {"name": "Broken Unit"},
        },
    }
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/ndiquery").respond(200, json=_ndiquery_body([doc]))
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            kind="both",
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert response.total_matching == 1
    assert len(response.units) == 1
    unit = response.units[0]
    assert isinstance(unit, SpikeSummaryUnit)
    assert unit.name == "Broken Unit"
    assert unit.error_kind == "decode_failed"
    assert unit.error is not None
    assert "no parseable spike_times" in unit.error
    # No data fields populated when decode failed.
    assert unit.spike_times is None
    assert unit.isi_intervals is None


@pytest.mark.asyncio
async def test_t_window_filters_spikes(cloud: NdiCloudClient) -> None:
    """Spikes outside [t0, t1] are filtered before stride-sampling."""
    dataset_id = "DS_SPIKE_7"
    # 0..9, with t_window=(2, 5) → keeps [2.0, 3.0, 4.0, 5.0].
    spikes = [float(i) for i in range(10)]
    doc = _make_doc("c" * 24, "Windowed Unit", spikes)
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}/documents/{'c' * 24}").respond(
            200, json=_detail_body(doc),
        )
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            unitDocId="c" * 24,
            kind="raster",
            tWindow=(2.0, 5.0),
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert len(response.units) == 1
    assert response.units[0].spike_times == [2.0, 3.0, 4.0, 5.0]


@pytest.mark.asyncio
async def test_camelcase_aliases_round_trip(cloud: NdiCloudClient) -> None:
    """The TS handler's camelCase fields (``datasetId``, ``unitDocId``,
    ``unitNameMatch``, ``tWindow``, ``maxUnits``) must populate the
    snake_case Python fields without translation.
    """
    request = SpikeSummaryRequest.model_validate({
        "datasetId": "DS_X",
        "unitDocId": "d" * 24,
        "unitNameMatch": "Saline",
        "kind": "both",
        "tWindow": [0.0, 10.0],
        "maxUnits": 7,
        "title": "Test",
    })
    assert request.dataset_id == "DS_X"
    assert request.unit_doc_id == "d" * 24
    assert request.unit_name_match == "Saline"
    assert request.t_window == (0.0, 10.0)
    assert request.max_units == 7
    assert request.title == "Test"


@pytest.mark.asyncio
async def test_default_max_units_when_unset(cloud: NdiCloudClient) -> None:
    """When max_units isn't provided, the service falls back to
    DEFAULT_MAX_UNITS (10) so callers don't accidentally pull the whole
    dataset's vmspikesummary set.
    """
    dataset_id = "DS_DEFAULT_CAP"
    docs_in = [
        _make_doc(f"d{i}", f"Unit {i:02d}", [float(i), float(i) + 0.1])
        for i in range(DEFAULT_MAX_UNITS + 3)
    ]
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/ndiquery").respond(200, json=_ndiquery_body(docs_in))
        ds = DocumentService(cloud)
        request = SpikeSummaryRequest(
            datasetId=dataset_id,
            kind="both",
        )
        response = await compute_spike_summary(
            request,
            document_service=ds,
            cloud=cloud,
            session=None,
        )

    assert response.total_matching == DEFAULT_MAX_UNITS + 3
    assert len(response.units) == DEFAULT_MAX_UNITS
