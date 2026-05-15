"""Unit tests for `aggregate_documents_service` — Stream 4.9 (2026-05-16).

Ports the TypeScript test scenarios from
`apps/web/tests/unit/ai/tools/aggregate-documents.test.ts` into pytest.
The service is stateless and only collaborates with `NdiCloudClient` via
`ndiquery`, so tests mock the cloud call and exercise the pure logic:

* Numeric extraction at dotted ``valueField``.
* Optional grouping at dotted ``groupBy``.
* Per-group summary statistics (count, mean, median, std, min, max).
* `numeric_matches` accounting — including the "has value but no group
  label" skip path that pre-fix used to inflate the count.
* `truncated` flag when the cloud returns more matches than ``max_docs``.
* `datasets_contributing` capped at REFERENCE_CAP.

Float comparisons use `math.isclose(rel_tol=1e-9)` because Python's
sample-std math uses N-1; values agree with the TS handler to ~14 digits.
"""
from __future__ import annotations

import math
from typing import Any

import pytest

from backend.services.aggregate_documents_service import (
    REFERENCE_CAP,
    AggregateDocumentsRequest,
    AggregateDocumentsService,
    _extract_numeric,
    _extract_string,
    _summary_stats,
)


class _StubCloud:
    """Test double for NdiCloudClient that records the ndiquery payload
    and returns a canned response. No HTTP."""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.calls: list[dict[str, Any]] = []

    async def ndiquery(
        self,
        *,
        searchstructure: list[dict[str, Any]],
        scope: str,
        access_token: str | None,
    ) -> dict[str, Any]:
        self.calls.append({
            "searchstructure": searchstructure,
            "scope": scope,
            "access_token": access_token,
        })
        return self._body


def _make_subject(
    doc_id: str,
    dataset_id: str,
    weight: float | str | None,
    strain: str | None = None,
) -> dict[str, Any]:
    """Helper: minimal subject doc shape matching what cloud-node emits."""
    return {
        "id": doc_id,
        "ndiId": f"ndi-{doc_id}",
        "datasetId": dataset_id,
        "document_class": {"class_name": "subject"},
        "data": {
            "subject": {
                "weight_grams": weight,
                "strain": strain,
            },
        },
    }


# ---------------------------------------------------------------------------
# Pure-helper tests (extraction + stats)
# ---------------------------------------------------------------------------

class TestExtractNumeric:
    def test_finds_int_at_dotted_path(self) -> None:
        doc = {"data": {"subject": {"weight_grams": 250}}}
        assert _extract_numeric(doc, "data.subject.weight_grams") == 250.0

    def test_finds_float(self) -> None:
        doc = {"data": {"x": 3.14}}
        assert _extract_numeric(doc, "data.x") == 3.14

    def test_coerces_string_numerics(self) -> None:
        doc = {"data": {"x": "42.5"}}
        assert _extract_numeric(doc, "data.x") == 42.5

    def test_returns_none_for_non_finite(self) -> None:
        doc1 = {"data": {"x": float("inf")}}
        doc2 = {"data": {"x": float("nan")}}
        assert _extract_numeric(doc1, "data.x") is None
        assert _extract_numeric(doc2, "data.x") is None

    def test_returns_none_for_missing_path(self) -> None:
        doc = {"data": {"y": 1}}
        assert _extract_numeric(doc, "data.x") is None

    def test_returns_none_for_unparseable_string(self) -> None:
        doc = {"data": {"x": "hello"}}
        assert _extract_numeric(doc, "data.x") is None

    def test_rejects_booleans(self) -> None:
        # Bools are int subclasses in Python but the TS helper rejected
        # them; preserve that contract so we don't accidentally aggregate
        # `True/False` as 1/0.
        doc = {"data": {"x": True}}
        assert _extract_numeric(doc, "data.x") is None


class TestExtractString:
    def test_finds_string(self) -> None:
        doc = {"data": {"subject": {"strain": "C57BL/6"}}}
        assert _extract_string(doc, "data.subject.strain") == "C57BL/6"

    def test_returns_none_for_empty_string(self) -> None:
        doc = {"data": {"x": ""}}
        assert _extract_string(doc, "data.x") is None

    def test_returns_none_for_missing_path(self) -> None:
        doc = {"data": {"y": "z"}}
        assert _extract_string(doc, "data.x") is None

    def test_coerces_booleans(self) -> None:
        doc = {"data": {"x": True}}
        assert _extract_string(doc, "data.x") == "true"

    def test_coerces_numbers(self) -> None:
        doc = {"data": {"x": 42}}
        assert _extract_string(doc, "data.x") == "42"


class TestSummaryStats:
    def test_count_mean_median_basic(self) -> None:
        stats = _summary_stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert stats["count"] == 5
        assert stats["mean"] == 3.0
        assert stats["median"] == 3.0
        assert stats["min"] == 1.0
        assert stats["max"] == 5.0
        # Sample std of [1,2,3,4,5] = sqrt(2.5) ≈ 1.5811
        assert math.isclose(stats["std"], math.sqrt(2.5), rel_tol=1e-9)

    def test_median_for_even_length(self) -> None:
        stats = _summary_stats([1.0, 2.0, 3.0, 4.0])
        assert stats["median"] == 2.5

    def test_singleton_has_zero_std(self) -> None:
        # n=1 → sample std undefined; TS returns 0; mirror that.
        stats = _summary_stats([42.0])
        assert stats["count"] == 1
        assert stats["mean"] == 42.0
        assert stats["median"] == 42.0
        assert stats["std"] == 0.0


# ---------------------------------------------------------------------------
# Service end-to-end (with stubbed cloud)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregates_a_single_group_when_groupby_unset() -> None:
    cloud = _StubCloud({
        "documents": [
            _make_subject("d1", "ds-A", 200.0),
            _make_subject("d2", "ds-A", 250.0),
            _make_subject("d3", "ds-A", 300.0),
        ],
        "totalItems": 3,
    })
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
    )

    result = await svc.aggregate(req, access_token=None)

    assert result["total_items"] == 3
    assert result["numeric_matches"] == 3
    assert result["scanned_docs"] == 3
    assert result["truncated"] is False
    assert result["valueField"] == "data.subject.weight_grams"
    assert len(result["groups"]) == 1
    g = result["groups"][0]
    assert g["group"] == "all"
    assert g["count"] == 3
    assert g["mean"] == 250.0
    assert g["median"] == 250.0
    assert g["min"] == 200.0
    assert g["max"] == 300.0
    assert g["sample_doc"] == {
        "id": "d1", "dataset_id": "ds-A", "class": "subject",
    }
    assert result["datasets_contributing"] == ["ds-A"]


@pytest.mark.asyncio
async def test_groups_by_dotted_path() -> None:
    cloud = _StubCloud({
        "documents": [
            _make_subject("d1", "ds-A", 200.0, strain="C57"),
            _make_subject("d2", "ds-A", 220.0, strain="C57"),
            _make_subject("d3", "ds-A", 250.0, strain="BALB"),
            _make_subject("d4", "ds-A", 260.0, strain="BALB"),
        ],
        "totalItems": 4,
    })
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
        groupBy="data.subject.strain",
    )

    result = await svc.aggregate(req, access_token=None)

    assert result["numeric_matches"] == 4
    groups = {g["group"]: g for g in result["groups"]}
    assert set(groups.keys()) == {"C57", "BALB"}
    assert groups["C57"]["count"] == 2
    assert groups["C57"]["mean"] == 210.0
    assert groups["BALB"]["mean"] == 255.0
    # Each group surfaces the FIRST contributing doc as its sample.
    assert groups["C57"]["sample_doc"]["id"] == "d1"
    assert groups["BALB"]["sample_doc"]["id"] == "d3"


@pytest.mark.asyncio
async def test_skips_docs_with_value_but_no_group_label() -> None:
    """The TS handler was fixed to NOT inflate ``numeric_matches`` when a
    doc has a finite numeric but the groupBy path is missing; otherwise
    "across 215 subjects" would claim more subjects than actually got
    bucketed."""
    cloud = _StubCloud({
        "documents": [
            _make_subject("d1", "ds-A", 200.0, strain="C57"),
            _make_subject("d2", "ds-A", 220.0, strain=None),    # no group
            _make_subject("d3", "ds-A", 250.0, strain="BALB"),
        ],
        "totalItems": 3,
    })
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
        groupBy="data.subject.strain",
    )

    result = await svc.aggregate(req, access_token=None)
    # 3 total matches, 1 dropped → 2 contributed.
    assert result["total_items"] == 3
    assert result["numeric_matches"] == 2


@pytest.mark.asyncio
async def test_skips_docs_with_no_numeric_value() -> None:
    cloud = _StubCloud({
        "documents": [
            _make_subject("d1", "ds-A", 200.0),
            _make_subject("d2", "ds-A", None),              # missing
            _make_subject("d3", "ds-A", "not a number"),    # unparseable
            _make_subject("d4", "ds-A", float("nan")),      # NaN
        ],
        "totalItems": 4,
    })
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
    )

    result = await svc.aggregate(req, access_token=None)
    assert result["total_items"] == 4
    assert result["numeric_matches"] == 1


@pytest.mark.asyncio
async def test_truncation_when_total_exceeds_max_docs() -> None:
    docs = [_make_subject(f"d{i}", "ds-A", 100.0 + i) for i in range(10)]
    cloud = _StubCloud({
        "documents": docs,
        "totalItems": 10,
    })
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
        maxDocs=5,
    )

    result = await svc.aggregate(req, access_token=None)
    assert result["scanned_docs"] == 5
    assert result["truncated"] is True
    assert result["total_items"] == 10
    # Only first 5 contributed.
    assert result["groups"][0]["count"] == 5


@pytest.mark.asyncio
async def test_datasets_contributing_capped_at_reference_cap() -> None:
    docs = [
        _make_subject(f"d{i}", f"ds-{i}", 100.0 + i)
        for i in range(REFERENCE_CAP + 5)
    ]
    cloud = _StubCloud({"documents": docs, "totalItems": len(docs)})
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
    )

    result = await svc.aggregate(req, access_token=None)
    # Cap kicks in — exactly REFERENCE_CAP distinct datasets surfaced.
    assert len(result["datasets_contributing"]) == REFERENCE_CAP


@pytest.mark.asyncio
async def test_handles_empty_cloud_response_gracefully() -> None:
    cloud = _StubCloud({"documents": [], "totalItems": 0})
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="public",
        searchstructure=[{"operation": "isa", "param1": "subject"}],
        valueField="data.subject.weight_grams",
    )

    result = await svc.aggregate(req, access_token=None)
    assert result["total_items"] == 0
    assert result["numeric_matches"] == 0
    assert result["groups"] == []
    assert result["datasets_contributing"] == []
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_forwards_searchstructure_and_scope_to_cloud() -> None:
    cloud = _StubCloud({"documents": [], "totalItems": 0})
    svc = AggregateDocumentsService(cloud)  # type: ignore[arg-type]
    req = AggregateDocumentsRequest(
        scope="abc1234567890123456789ab,def1234567890123456789ab",
        searchstructure=[
            {"operation": "isa", "param1": "subject"},
            {"operation": "contains_string", "field": "subject.strain", "param1": "C57"},
        ],
        valueField="data.subject.weight_grams",
    )

    await svc.aggregate(req, access_token=None)
    assert len(cloud.calls) == 1
    call = cloud.calls[0]
    assert call["scope"] == "abc1234567890123456789ab,def1234567890123456789ab"
    assert len(call["searchstructure"]) == 2
    assert call["searchstructure"][0] == {"operation": "isa", "param1": "subject"}
    assert call["access_token"] is None


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

class TestAggregateDocumentsRequestValidation:
    def test_rejects_invalid_scope(self) -> None:
        with pytest.raises(ValueError):
            AggregateDocumentsRequest(
                scope="not-a-keyword-or-csv-of-hex",
                searchstructure=[{"operation": "isa", "param1": "subject"}],
                valueField="data.x",
            )

    def test_rejects_empty_searchstructure(self) -> None:
        with pytest.raises(ValueError):
            AggregateDocumentsRequest(
                scope="public",
                searchstructure=[],
                valueField="data.x",
            )

    def test_rejects_oversize_searchstructure(self) -> None:
        with pytest.raises(ValueError):
            AggregateDocumentsRequest(
                scope="public",
                searchstructure=[{"operation": "isa", "param1": "x"}] * 21,
                valueField="data.x",
            )

    def test_rejects_max_docs_above_ceiling(self) -> None:
        with pytest.raises(ValueError):
            AggregateDocumentsRequest(
                scope="public",
                searchstructure=[{"operation": "isa", "param1": "subject"}],
                valueField="data.x",
                maxDocs=50_001,
            )

    def test_accepts_public_scope(self) -> None:
        req = AggregateDocumentsRequest(
            scope="public",
            searchstructure=[{"operation": "isa", "param1": "subject"}],
            valueField="data.x",
        )
        assert req.scope == "public"

    def test_accepts_csv_dataset_id_scope(self) -> None:
        req = AggregateDocumentsRequest(
            scope="abc1234567890123456789ab",
            searchstructure=[{"operation": "isa", "param1": "subject"}],
            valueField="data.x",
        )
        assert req.scope == "abc1234567890123456789ab"
