"""Unit tests for TabularQueryService.

Tests focus on the aggregation math + edge cases. The SummaryTableService
dependency is stubbed — its own tests cover the ontologyTableRow
projection logic.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.services.tabular_query_service import (
    MAX_GROUPS,
    MAX_VALUES_PER_GROUP,
    TabularQueryService,
    _percentile,
    _stride_sample,
    _summary_stats,
)

# ---------------------------------------------------------------------------
# Stat-helper unit tests — pure functions, no IO
# ---------------------------------------------------------------------------


class TestSummaryStats:
    def test_basic_stats(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        s = _summary_stats(vals)
        assert s["count"] == 5
        assert s["mean"] == 3.0
        assert s["median"] == 3.0
        assert s["min"] == 1.0
        assert s["max"] == 5.0
        assert abs(s["std"] - 1.5811) < 0.001
        assert s["q1"] == 2.0
        assert s["q3"] == 4.0

    def test_single_value_zero_std(self):
        s = _summary_stats([7.0])
        assert s["count"] == 1
        assert s["std"] == 0.0
        assert s["mean"] == 7.0

    def test_two_values(self):
        s = _summary_stats([10.0, 20.0])
        assert s["count"] == 2
        assert s["mean"] == 15.0
        assert s["median"] == 15.0


class TestPercentile:
    def test_quartiles(self):
        assert _percentile([1, 2, 3, 4, 5], 25) == 2.0
        assert _percentile([1, 2, 3, 4, 5], 50) == 3.0
        assert _percentile([1, 2, 3, 4, 5], 75) == 4.0

    def test_endpoints(self):
        assert _percentile([1, 2, 3, 4, 5], 0) == 1.0
        assert _percentile([1, 2, 3, 4, 5], 100) == 5.0

    def test_empty_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_value(self):
        assert _percentile([42.0], 50) == 42.0


class TestStrideSample:
    def test_under_cap_returns_all(self):
        assert _stride_sample([1.0, 2.0, 3.0], cap=10) == [1.0, 2.0, 3.0]

    def test_over_cap_preserves_endpoints(self):
        vals = [float(i) for i in range(100)]
        out = _stride_sample(vals, cap=10)
        assert len(out) == 10
        assert out[0] == 0.0
        assert out[-1] == 99.0


# ---------------------------------------------------------------------------
# Service-level: stub SummaryTableService with the real ontology_tables
# response shape (one group per distinct variableNames schema, rows are
# dicts keyed by variableName).
# ---------------------------------------------------------------------------


def _make_ontology_response(
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    doc_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a one-group ontology_tables response matching the real
    shape returned by SummaryTableService.ontology_tables.
    """
    return {
        "groups": [
            {
                "variableNames": [c["key"] for c in columns],
                "names": [c.get("label", c["key"]) for c in columns],
                "ontologyNodes": [c.get("ontologyTerm") for c in columns],
                "table": {"columns": columns, "rows": rows},
                "docIds": doc_ids or [],
                "rowCount": len(rows),
            },
        ],
    }


class _FakeSummaryService:
    """Stub for SummaryTableService — returns a canned ontology_tables payload."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    async def ontology_tables(
        self,
        dataset_id: str,  # noqa: ARG002 — stub mirrors the real signature
        *,
        session: Any,  # noqa: ARG002 — stub mirrors the real signature
    ) -> dict[str, Any]:
        return self._response


@pytest.mark.asyncio
async def test_violin_groups_basic():
    """Two-group violin keyed on a column label substring."""
    columns = [
        {"key": "treatment_group", "label": "treatment_group"},
        {"key": "EPM_OpenArm_Entries", "label": "EPM Open Arm Entries"},
    ]
    rows = [
        {"treatment_group": "Saline", "EPM_OpenArm_Entries": 5.0},
        {"treatment_group": "Saline", "EPM_OpenArm_Entries": 7.0},
        {"treatment_group": "Saline", "EPM_OpenArm_Entries": 6.0},
        {"treatment_group": "CNO", "EPM_OpenArm_Entries": 2.0},
        {"treatment_group": "CNO", "EPM_OpenArm_Entries": 3.0},
        {"treatment_group": "CNO", "EPM_OpenArm_Entries": 1.0},
    ]
    response = _make_ontology_response(columns, rows, doc_ids=["doc_abc"])
    svc = TabularQueryService(_FakeSummaryService(response))  # type: ignore[arg-type]
    result = await svc.violin_groups(
        "dataset_xyz",
        "OpenArm",
        group_by="treatment_group",
        group_order=None,
        session=None,
    )
    assert len(result["groups"]) == 2
    by_name = {g["name"]: g for g in result["groups"]}
    assert by_name["Saline"]["mean"] == 6.0
    assert by_name["CNO"]["mean"] == 2.0
    assert by_name["Saline"]["count"] == 3
    assert result["source"]["document_id"] == "doc_abc"
    assert result["xLabel"] == "treatment_group"
    # Label comes from the human-readable column label, not the raw key.
    assert "Open Arm Entries" in result["yLabel"]


@pytest.mark.asyncio
async def test_violin_groups_no_match_returns_empty_with_meta():
    columns = [{"key": "unrelated", "label": "Unrelated Variable"}]
    rows = [{"unrelated": 1.0}]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "ElevatedPlusMaze", group_by="g", group_order=None, session=None,
    )
    assert result["groups"] == []
    assert "no ontologyTableRow column matched" in result["_meta"]["reason"]


@pytest.mark.asyncio
async def test_violin_groups_respects_group_order():
    columns = [
        {"key": "group", "label": "group"},
        {"key": "y", "label": "y"},
    ]
    rows = [
        {"group": "A", "y": 1.0},
        {"group": "B", "y": 2.0},
        {"group": "C", "y": 3.0},
    ]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "y", group_by="group", group_order=["C", "A"], session=None,
    )
    names = [g["name"] for g in result["groups"]]
    # C and A specified first; B (unspecified) appears after.
    assert names == ["C", "A", "B"]


@pytest.mark.asyncio
async def test_violin_groups_no_group_by_makes_single_group():
    columns = [{"key": "y", "label": "Value"}]
    rows = [{"y": 1.0}, {"y": 2.0}, {"y": 3.0}, {"y": 4.0}]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "Value", group_by=None, group_order=None, session=None,
    )
    assert len(result["groups"]) == 1
    assert result["groups"][0]["name"] == "all"
    assert result["groups"][0]["count"] == 4


@pytest.mark.asyncio
async def test_violin_groups_caps_group_count():
    columns = [{"key": "g", "label": "g"}, {"key": "y", "label": "y"}]
    rows = [
        {"g": f"g{i}", "y": float(i)} for i in range(MAX_GROUPS + 5)
    ]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "y", group_by="g", group_order=None, session=None,
    )
    assert len(result["groups"]) == MAX_GROUPS


@pytest.mark.asyncio
async def test_violin_groups_caps_values_per_group_but_stats_use_full():
    """Stats are computed BEFORE the value-list sampling so they remain accurate."""
    columns = [{"key": "g", "label": "g"}, {"key": "y", "label": "Value"}]
    n = MAX_VALUES_PER_GROUP + 200
    rows = [{"g": "all", "y": float(i)} for i in range(n)]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "Value", group_by="g", group_order=None, session=None,
    )
    g = result["groups"][0]
    assert len(g["values"]) <= MAX_VALUES_PER_GROUP
    expected_mean = (n - 1) / 2
    assert abs(g["mean"] - expected_mean) < 0.001
    assert g["count"] == n


@pytest.mark.asyncio
async def test_violin_groups_skips_nonfinite_values():
    """NaN / inf rows shouldn't blow up the aggregation."""
    columns = [{"key": "g", "label": "g"}, {"key": "y", "label": "y"}]
    rows = [
        {"g": "A", "y": 1.0},
        {"g": "A", "y": 2.0},
        {"g": "A", "y": float("nan")},
        {"g": "A", "y": float("inf")},
        {"g": "B", "y": 5.0},
    ]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "y", group_by="g", group_order=None, session=None,
    )
    by_name = {g["name"]: g for g in result["groups"]}
    assert by_name["A"]["count"] == 2
    assert by_name["A"]["mean"] == 1.5


@pytest.mark.asyncio
async def test_violin_groups_empty_substring_returns_empty():
    columns = [{"key": "y", "label": "y"}]
    rows = [{"y": 1.0}]
    svc = TabularQueryService(
        _FakeSummaryService(_make_ontology_response(columns, rows)),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "", group_by=None, group_order=None, session=None,
    )
    assert result["groups"] == []
    assert "empty" in result["_meta"]["reason"]


@pytest.mark.asyncio
async def test_violin_groups_no_ontology_docs_returns_empty():
    svc = TabularQueryService(
        _FakeSummaryService({"groups": []}),  # type: ignore[arg-type]
    )
    result = await svc.violin_groups(
        "ds", "anything", group_by=None, group_order=None, session=None,
    )
    assert result["groups"] == []
    assert "no ontologyTableRow docs" in result["_meta"]["reason"]
