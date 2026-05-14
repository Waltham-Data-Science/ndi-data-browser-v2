"""Unit tests for :class:`TreatmentTimelineService`.

Both backing services are stubbed: the orchestrator under test
composes two existing services that have their own coverage. The
focus here is the projection math — ordinal slot timing, explicit
timing detection, mixed mode classification, subject cap, fallback
fan-out, and the ``empty_hint`` envelope.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.treatment_timeline_service import (
    DEFAULT_MAX_SUBJECTS,
    TreatmentTimelineService,
    _classify_temporal_source,
    _extract_explicit_timing,
    _parse_iso_datetime,
    _pick_subject_label,
    _pick_treatment_label,
)

# ---------------------------------------------------------------------------
# Pure-helper tests — no IO
# ---------------------------------------------------------------------------


class TestPickSubjectLabel:
    def test_prefers_subject_document_identifier(self):
        row = {"subjectDocumentIdentifier": "subj_001", "subject": "ignored"}
        assert _pick_subject_label(row) == "subj_001"

    def test_falls_back_to_subject_field(self):
        row = {"subject": "subj_alt"}
        assert _pick_subject_label(row) == "subj_alt"

    def test_returns_none_when_empty(self):
        assert _pick_subject_label({}) is None
        assert _pick_subject_label({"subjectDocumentIdentifier": ""}) is None


class TestPickTreatmentLabel:
    def test_prefers_treatment_name(self):
        row = {"treatmentName": "Saline", "stringValue": "ignored"}
        assert _pick_treatment_label(row) == "Saline"

    def test_falls_back_to_string_value(self):
        row = {"stringValue": "CNO"}
        assert _pick_treatment_label(row) == "CNO"

    def test_returns_none_when_empty(self):
        assert _pick_treatment_label({}) is None


class TestExtractExplicitTiming:
    def test_numeric_value_pair(self):
        row = {"numericValue": [10.0, 20.0]}
        assert _extract_explicit_timing(row) == (10.0, 20.0)

    def test_numeric_value_scalar(self):
        row = {"numericValue": 5.0}
        assert _extract_explicit_timing(row) == (5.0, 6.0)

    def test_numeric_value_singleton_array(self):
        row = {"numericValue": [7.5]}
        assert _extract_explicit_timing(row) == (7.5, 8.5)

    def test_start_date_pair(self):
        row = {"startDate": "2026-01-01", "endDate": "2026-01-05"}
        assert _extract_explicit_timing(row) == ("2026-01-01", "2026-01-05")

    def test_numeric_value_empty_array_returns_none(self):
        assert _extract_explicit_timing({"numericValue": []}) is None

    def test_nan_inf_rejected(self):
        assert _extract_explicit_timing({"numericValue": float("nan")}) is None
        assert _extract_explicit_timing({"numericValue": float("inf")}) is None

    def test_no_timing_returns_none(self):
        assert _extract_explicit_timing({"treatmentName": "Saline"}) is None

    def test_iso_date_string_value_emits_day_window(self):
        out = _extract_explicit_timing({"stringValue": "2026-05-14"})
        assert out is not None
        start, end = out
        assert start == "2026-05-14"
        # End is the +1 day ISO string — bare date interpreted as UTC.
        assert isinstance(end, str) and end.startswith("2026-05-15")


class TestParseIsoDatetime:
    def test_bare_date(self):
        out = _parse_iso_datetime("2026-05-14")
        assert out is not None
        assert out.year == 2026 and out.month == 5 and out.day == 14

    def test_z_suffix(self):
        out = _parse_iso_datetime("2026-05-14T12:00:00Z")
        assert out is not None
        assert out.hour == 12

    def test_garbage_returns_none(self):
        assert _parse_iso_datetime("not a date") is None


class TestClassifyTemporalSource:
    def test_all_explicit(self):
        assert _classify_temporal_source(5, 0) == "explicit"

    def test_all_ordinal(self):
        assert _classify_temporal_source(0, 5) == "ordinal"

    def test_mixed(self):
        assert _classify_temporal_source(3, 2) == "mixed"

    def test_neither_defaults_ordinal(self):
        assert _classify_temporal_source(0, 0) == "ordinal"


# ---------------------------------------------------------------------------
# Service-level: stub both backing services
# ---------------------------------------------------------------------------


def _make_service(
    *,
    primary_response: dict[str, Any] | None = None,
    primary_raises: Exception | None = None,
    fallback_response: dict[str, Any] | None = None,
    fallback_raises: Exception | None = None,
) -> TreatmentTimelineService:
    """Compose a service whose backing dependencies return canned
    payloads. Either response or raises wins — use raises to simulate
    cloud failures.
    """
    summary = AsyncMock()
    if primary_raises is not None:
        summary.single_class.side_effect = primary_raises
    else:
        summary.single_class.return_value = primary_response or {
            "columns": [],
            "rows": [],
        }

    tabular = AsyncMock()
    if fallback_raises is not None:
        tabular.violin_groups.side_effect = fallback_raises
    else:
        tabular.violin_groups.return_value = fallback_response or {
            "groups": [],
            "yLabel": "",
            "xLabel": "",
        }

    return TreatmentTimelineService(summary=summary, tabular=tabular)


@pytest.mark.asyncio
async def test_primary_happy_path_explicit_timing():
    """5 treatments across 3 subjects with explicit numericValue —
    items returned in first-seen subject order, all timing
    explicit so temporal_source='explicit'.
    """
    rows = [
        {
            "subjectDocumentIdentifier": "subj_A",
            "treatmentName": "Saline",
            "numericValue": [0.0, 10.0],
        },
        {
            "subjectDocumentIdentifier": "subj_A",
            "treatmentName": "CNO",
            "numericValue": [10.0, 20.0],
        },
        {
            "subjectDocumentIdentifier": "subj_B",
            "treatmentName": "Saline",
            "numericValue": [0.0, 15.0],
        },
        {
            "subjectDocumentIdentifier": "subj_C",
            "treatmentName": "Saline",
            "numericValue": [0.0, 12.0],
        },
        {
            "subjectDocumentIdentifier": "subj_C",
            "treatmentName": "CNO",
            "numericValue": [12.0, 24.0],
        },
    ]
    svc = _make_service(
        primary_response={
            "columns": [{"key": "treatmentName"}, {"key": "subjectDocumentIdentifier"}],
            "rows": rows,
        },
    )
    result = await svc.compute_timeline(
        "ds_xyz", title="My Timeline", max_subjects=30, session=None,
    )
    assert result["total_subjects"] == 3
    assert result["total_treatments"] == 5
    assert result["temporal_source"] == "explicit"
    assert result["title"] == "My Timeline"
    assert result["datasetId"] == "ds_xyz"
    # First-seen ordering of subjects: A, B, C.
    subjects = [item["subject"] for item in result["items"]]
    assert subjects == ["subj_A", "subj_A", "subj_B", "subj_C", "subj_C"]
    # Timing is the literal numericValue pair.
    assert result["items"][0]["start"] == 0.0
    assert result["items"][0]["end"] == 10.0
    # No empty_hint when items are produced.
    assert "empty_hint" not in result


@pytest.mark.asyncio
async def test_ordinal_timing_when_numeric_value_missing():
    """Rows without any explicit timing get per-subject ordinal slots
    [0,1], [1,2], etc. temporal_source='ordinal'.
    """
    rows = [
        {"subjectDocumentIdentifier": "S1", "treatmentName": "T1"},
        {"subjectDocumentIdentifier": "S1", "treatmentName": "T2"},
        {"subjectDocumentIdentifier": "S1", "treatmentName": "T3"},
        {"subjectDocumentIdentifier": "S2", "treatmentName": "T1"},
        {"subjectDocumentIdentifier": "S2", "treatmentName": "T2"},
    ]
    svc = _make_service(primary_response={"columns": [], "rows": rows})
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["temporal_source"] == "ordinal"
    assert result["total_subjects"] == 2
    assert result["total_treatments"] == 5
    items = result["items"]
    # S1's three treatments: [0,1], [1,2], [2,3].
    s1 = [it for it in items if it["subject"] == "S1"]
    assert [it["start"] for it in s1] == [0, 1, 2]
    assert [it["end"] for it in s1] == [1, 2, 3]
    # S2's two treatments: [0,1], [1,2]. Per-subject counter resets.
    s2 = [it for it in items if it["subject"] == "S2"]
    assert [it["start"] for it in s2] == [0, 1]
    assert [it["end"] for it in s2] == [1, 2]


@pytest.mark.asyncio
async def test_mixed_timing_classification():
    """Some rows explicit, some ordinal → temporal_source='mixed'."""
    rows = [
        # Explicit timing.
        {
            "subjectDocumentIdentifier": "S1",
            "treatmentName": "T1",
            "numericValue": [0.0, 5.0],
        },
        # Same subject, ordinal — counter is independent of the
        # explicit row's range.
        {"subjectDocumentIdentifier": "S1", "treatmentName": "T2"},
        # Different subject, also explicit.
        {
            "subjectDocumentIdentifier": "S2",
            "treatmentName": "T1",
            "numericValue": [0.0, 10.0],
        },
    ]
    svc = _make_service(primary_response={"columns": [], "rows": rows})
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["temporal_source"] == "mixed"
    # 2 explicit + 1 ordinal = 3 total items.
    assert result["total_treatments"] == 3


@pytest.mark.asyncio
async def test_max_subjects_cap_drops_excess():
    """50 distinct subjects with maxSubjects=30 → only 30 surface in
    ``items``. total_subjects reflects the in-chart count (30), not the
    underlying count — that's the TS handler's contract: the chart
    truncates and the caller surfaces the truncation count via the
    ``cited`` vs ``total_subjects`` ratio at the chat-prompt layer.
    """
    rows = [
        {"subjectDocumentIdentifier": f"subj_{i}", "treatmentName": "Saline"}
        for i in range(50)
    ]
    svc = _make_service(primary_response={"columns": [], "rows": rows})
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["total_subjects"] == 30
    assert result["total_treatments"] == 30
    # Distinct subjects in items.
    distinct = {it["subject"] for it in result["items"]}
    assert len(distinct) == 30


@pytest.mark.asyncio
async def test_primary_empty_fallback_hits_synthesizes_group_rows():
    """Zero treatment rows; tabular_query has 2 groups. Synthesize one
    bar per group with subject='group:<name>' and ordinal timing.
    """
    fallback = {
        "groups": [
            {"name": "Saline", "count": 12},
            {"name": "CNO", "count": 9},
        ],
        "yLabel": "Treatment: CNO or Saline Administration",
        "xLabel": "",
    }
    svc = _make_service(
        primary_response={"columns": [], "rows": []},
        fallback_response=fallback,
    )
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["total_subjects"] == 2
    assert result["total_treatments"] == 2
    # Subject labels prefixed with group: so callers can disambiguate
    # synthesized vs real subject rows.
    subjects = sorted(it["subject"] for it in result["items"])
    assert subjects == ["group:CNO", "group:Saline"]
    treatments = sorted(it["treatment"] for it in result["items"])
    assert treatments == ["CNO", "Saline"]
    # All synthesized → temporal_source='ordinal'.
    assert result["temporal_source"] == "ordinal"


@pytest.mark.asyncio
async def test_primary_empty_fallback_empty_surfaces_empty_hint():
    """Both backends return nothing — empty_hint surfaced with the
    'no temporal info' reason and available_columns from whatever
    column list the primary did expose.
    """
    svc = _make_service(
        primary_response={
            "columns": [
                {"key": "treatmentName"},
                {"key": "subjectDocumentIdentifier"},
            ],
            "rows": [],
        },
        fallback_response={"groups": [], "yLabel": "", "xLabel": ""},
    )
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["items"] == []
    assert result["total_subjects"] == 0
    assert result["total_treatments"] == 0
    assert "empty_hint" in result
    hint = result["empty_hint"]
    assert "no temporal info" in hint["reason"]
    # available_columns echoes the column keys the primary table
    # exposed even though the row list was empty — gives the caller
    # something to mention.
    assert "treatmentName" in hint["available_columns"]
    assert "subjectDocumentIdentifier" in hint["available_columns"]


# ---------------------------------------------------------------------------
# Defensive edge cases — behavior not strictly required by the brief but
# locked here to prevent regressions when the orchestrator evolves.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_without_subject_or_treatment_dropped():
    """Rows missing subject OR treatment are silently skipped — they
    can't be plotted. No empty_hint when at least one row plots.
    """
    rows = [
        # Plottable.
        {"subjectDocumentIdentifier": "S1", "treatmentName": "T1"},
        # Missing subject.
        {"treatmentName": "T2"},
        # Missing treatment.
        {"subjectDocumentIdentifier": "S2"},
    ]
    svc = _make_service(primary_response={"columns": [], "rows": rows})
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["total_treatments"] == 1
    assert "empty_hint" not in result


@pytest.mark.asyncio
async def test_rows_returned_but_unplottable_surfaces_hint():
    """When rows come back but NONE have a usable subject+treatment
    pair, the hint reason distinguishes that from the empty-rows case.
    """
    rows = [
        {"treatmentName": "T1"},  # No subject.
        {"subjectDocumentIdentifier": "S1"},  # No treatment.
    ]
    svc = _make_service(primary_response={"columns": [], "rows": rows})
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["items"] == []
    assert "empty_hint" in result
    assert "none had a usable" in result["empty_hint"]["reason"]


@pytest.mark.asyncio
async def test_primary_failure_falls_through_to_fallback():
    """If the primary call raises (cloud unreachable, etc.), the
    service catches and tries the fallback — does NOT propagate the
    error out of compute_timeline.
    """
    svc = _make_service(
        primary_raises=RuntimeError("cloud unreachable"),
        fallback_response={
            "groups": [{"name": "Saline"}],
            "yLabel": "Treatment",
            "xLabel": "",
        },
    )
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    # Fallback produced a row; no error surfaced.
    assert result["total_treatments"] == 1
    assert result["items"][0]["subject"] == "group:Saline"


@pytest.mark.asyncio
async def test_both_failures_surface_empty_hint_not_exception():
    """Catastrophic — both backends raise. The endpoint still returns
    a well-typed response with empty_hint set.
    """
    svc = _make_service(
        primary_raises=RuntimeError("primary down"),
        fallback_raises=RuntimeError("fallback down"),
    )
    result = await svc.compute_timeline(
        "ds_a", title=None, max_subjects=30, session=None,
    )
    assert result["items"] == []
    assert "empty_hint" in result


@pytest.mark.asyncio
async def test_default_max_subjects_constant_used_by_router():
    """The router uses DEFAULT_MAX_SUBJECTS as the model default; lock
    the constant value so a silent bump in the service doesn't change
    the public contract.
    """
    assert DEFAULT_MAX_SUBJECTS == 30
