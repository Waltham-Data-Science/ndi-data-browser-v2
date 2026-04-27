"""DatasetService.list_*_with_summaries — Plan B B2 catalog enricher.

Exercises:
  - happy path: 3 datasets, each gets a compact summary, parallelism bounded
    by Semaphore(3) (verified via an in-flight counter).
  - per-dataset failure: one synth raises, the page still renders with
    ``summary: null`` on the failing row, sibling rows unaffected.
  - zero-summary short-circuit (ndi-cloud-node#15): if the cloud-list
    response already carries ``species``/``brainRegions``/``numberOfSubjects``,
    the enricher skips the synthesizer entirely and builds the compact
    summary from cloud-provided fields.
  - empty list: no-op.
  - row without id: ``summary: null``.
  - perf smoke: 20-dataset page with a simulated 50ms synth latency — total
    wall time bounded by ceil(20/3) * 50ms ≈ 350ms, proving concurrency
    works.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import backend.services.dataset_service as ds_svc_module
from backend.services.dataset_service import (
    MAX_CONCURRENT_SUMMARIES,
    PER_ROW_SUMMARY_TIMEOUT_SECONDS,
    DatasetService,
    _compact_summary_from_cloud_fields,
    _csv_to_ontology_terms,
    _row_dataset_id,
)
from backend.services.dataset_summary_service import (
    CompactDatasetSummary,
    DatasetSummary,
    DatasetSummaryCitation,
    DatasetSummaryCounts,
    DatasetSummaryDateRange,
    DatasetSummaryService,
    OntologyTerm,
)

# ---------------------------------------------------------------------------
# Full-shape fixture helpers
# ---------------------------------------------------------------------------

def _full_summary(
    dataset_id: str,
    *,
    subjects: int = 5,
    total_documents: int = 120,
    species_label: str = "Rattus norvegicus",
    species_ontology: str = "NCBITaxon:10116",
    brain_region: str = "primary visual cortex",
    brain_ontology: str = "UBERON:0002436",
) -> DatasetSummary:
    return DatasetSummary(
        datasetId=dataset_id,
        counts=DatasetSummaryCounts(
            sessions=1, subjects=subjects, probes=2, elements=2,
            epochs=8, totalDocuments=total_documents,
        ),
        species=[OntologyTerm(label=species_label, ontologyId=species_ontology)],
        strains=[OntologyTerm(label="N2", ontologyId="WBStrain:00000001")],
        sexes=[OntologyTerm(label="female", ontologyId="PATO:0000383")],
        brainRegions=[OntologyTerm(label=brain_region, ontologyId=brain_ontology)],
        probeTypes=["n-trode"],
        dateRange=DatasetSummaryDateRange(
            earliest="2025-06-01T00:00:00Z", latest="2026-02-01T00:00:00Z",
        ),
        totalSizeBytes=1_048_576,
        citation=DatasetSummaryCitation(
            title=f"Dataset {dataset_id}",
            license="CC-BY-4.0",
            datasetDoi=f"https://doi.org/10.63884/{dataset_id.lower()}",
            paperDois=[],
            contributors=[],
            year=2025,
        ),
        computedAt="2026-04-17T00:00:00Z",
        extractionWarnings=[],
    )


def _mk_service_with_mock_summary(
    summaries: dict[str, DatasetSummary],
    *,
    max_in_flight: list[int] | None = None,
    failures: set[str] | None = None,
    per_call_delay: float = 0.0,
) -> tuple[DatasetService, DatasetSummaryService]:
    """Build a DatasetService paired with a mocked DatasetSummaryService
    whose ``build_summary`` returns the provided fixture (or raises for any
    dataset_id in ``failures``). ``max_in_flight`` records the peak
    concurrent invocations so the Semaphore bound can be asserted.
    """
    failures = failures or set()
    in_flight = 0
    peak = [0]  # mutable cell

    async def _build(dataset_id: str, *, session: Any = None) -> DatasetSummary:
        nonlocal in_flight
        in_flight += 1
        peak[0] = max(peak[0], in_flight)
        try:
            if per_call_delay:
                await asyncio.sleep(per_call_delay)
            if dataset_id in failures:
                raise RuntimeError(f"synth failed for {dataset_id}")
            if dataset_id not in summaries:
                raise KeyError(dataset_id)
            return summaries[dataset_id]
        finally:
            in_flight -= 1

    summary_svc = Mock(spec=DatasetSummaryService)
    summary_svc.build_summary = AsyncMock(side_effect=_build)

    cloud = Mock()
    svc = DatasetService(cloud)

    if max_in_flight is not None:
        # Pipe the peak back to the caller via the list reference.
        max_in_flight.append(peak[0])
        # Also attach the live reference so the caller can read after the call.
        max_in_flight.clear()
        max_in_flight.extend(peak)

    # Always return the peak reference so the caller can read it post-await.
    svc._peak_concurrency_for_tests = peak  # type: ignore[attr-defined]
    return svc, summary_svc


# ---------------------------------------------------------------------------
# Happy path — every row gets a compact summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_attaches_compact_summary_to_each_row() -> None:
    summaries = {
        "D1": _full_summary("D1", subjects=3, total_documents=50),
        "D2": _full_summary("D2", subjects=7, total_documents=140),
        "D3": _full_summary("D3", subjects=12, total_documents=300),
    }
    svc, summary_svc = _mk_service_with_mock_summary(summaries)

    payload: dict[str, Any] = {
        "totalNumber": 3,
        "datasets": [
            {"id": "D1", "name": "One"},
            {"id": "D2", "name": "Two"},
            {"id": "D3", "name": "Three"},
        ],
    }
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )

    datasets = payload["datasets"]
    assert len(datasets) == 3
    # Row 1 — shape checks.
    assert datasets[0]["id"] == "D1"
    assert datasets[0]["summary"] is not None
    s1 = datasets[0]["summary"]
    assert s1["datasetId"] == "D1"
    assert s1["schemaVersion"] == "summary:v1"
    assert s1["counts"]["subjects"] == 3
    assert s1["counts"]["totalDocuments"] == 50
    assert s1["species"][0]["label"] == "Rattus norvegicus"
    assert s1["brainRegions"][0]["ontologyId"] == "UBERON:0002436"
    assert s1["citation"]["title"] == "Dataset D1"
    assert s1["citation"]["license"] == "CC-BY-4.0"
    assert s1["citation"]["year"] == 2025

    # Compact shape = ONLY the agreed keys (no probeTypes / strains /
    # sexes / contributors / extractionWarnings / computedAt / dateRange /
    # totalSizeBytes).
    assert set(s1.keys()) == {
        "datasetId", "counts", "species", "brainRegions",
        "citation", "schemaVersion",
    }
    assert set(s1["counts"].keys()) == {"subjects", "totalDocuments"}
    assert set(s1["citation"].keys()) == {
        "title", "license", "datasetDoi", "year",
    }


# ---------------------------------------------------------------------------
# Semaphore(3) bounds fanout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_bounds_concurrency_at_semaphore_limit() -> None:
    """With MAX_CONCURRENT_SUMMARIES=3 and 10 datasets each taking 50ms, the
    peak in-flight must land in the window ``[2, 3]``:

    - ``peak <= 3`` — the semaphore must bound fanout at or below the limit.
    - ``peak >= 2`` — actual concurrency must have occurred. A trivially
      serialized implementation (no semaphore, plain for-loop awaiting each
      build) would produce ``peak == 1`` and silently pass the upper bound,
      which is what the independent review flagged.

    50ms per call x 10 calls = 500ms serial; under Semaphore(3) we expect
    ~200ms wall time. Even on a loaded CI runner that stretches scheduling
    latency, at least two calls should be in-flight at some point during
    the run — asserting ``>= 2`` is the floor below which we'd consider
    the semaphore broken.
    """
    n = 10
    summaries = {f"D{i}": _full_summary(f"D{i}") for i in range(n)}
    svc, summary_svc = _mk_service_with_mock_summary(
        summaries, per_call_delay=0.05,
    )

    payload: dict[str, Any] = {
        "totalNumber": n,
        "datasets": [{"id": f"D{i}", "name": f"n{i}"} for i in range(n)],
    }
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )

    peak = svc._peak_concurrency_for_tests[0]  # type: ignore[attr-defined]
    assert 2 <= peak <= MAX_CONCURRENT_SUMMARIES, (
        f"peak={peak}, expected 2 <= peak <= {MAX_CONCURRENT_SUMMARIES} "
        f"(peak<2 means no concurrency happened; peak>3 means semaphore broken)"
    )

    # All rows should still have a summary.
    for row in payload["datasets"]:
        assert row["summary"] is not None


# ---------------------------------------------------------------------------
# Per-dataset failure leaves `summary: None` but sibling rows are fine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_downgrades_failed_rows_to_null_summary() -> None:
    summaries = {
        "OK1": _full_summary("OK1"),
        "OK2": _full_summary("OK2"),
    }
    svc, summary_svc = _mk_service_with_mock_summary(
        summaries, failures={"BROKEN"},
    )

    payload: dict[str, Any] = {
        "totalNumber": 3,
        "datasets": [
            {"id": "OK1", "name": "Ok1"},
            {"id": "BROKEN", "name": "Bad"},
            {"id": "OK2", "name": "Ok2"},
        ],
    }
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )

    rows = payload["datasets"]
    assert rows[0]["summary"] is not None
    assert rows[1]["summary"] is None  # failure → null
    assert rows[2]["summary"] is not None
    # Sanity: the failing row's raw fields remain untouched.
    assert rows[1]["id"] == "BROKEN"
    assert rows[1]["name"] == "Bad"


# ---------------------------------------------------------------------------
# Per-row timeout belt-and-suspenders (PR #98)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_times_out_stuck_row_to_null_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth (paired with PR #97): a single ``build_summary``
    call that hangs past :data:`PER_ROW_SUMMARY_TIMEOUT_SECONDS` must
    degrade to ``summary: null`` for that row WITHOUT failing the whole
    list and WITHOUT blocking the sibling rows beyond the timeout
    window.

    Before this belt, an unbounded per-row await could pin the catalog
    at ~90s in production (FastAPI's full 30s x 3 retry budget) when one
    synth got stuck on a slow cloud fan-out.

    Test mechanism:
      - Patch :data:`PER_ROW_SUMMARY_TIMEOUT_SECONDS` down to 0.05s so
        we don't sit on the real 5s during CI.
      - Mock ``build_summary`` to hang ~10x the timeout for the
        ``HANGS`` row, return promptly for OK rows.
      - Assert: HTTP 200 (no 500), HANGS row has ``summary: null``,
        sibling rows still get their summaries, total wall time well
        below the hang duration.
    """
    test_timeout = 0.05  # 50ms — far short of the real 5s default.
    monkeypatch.setattr(
        ds_svc_module, "PER_ROW_SUMMARY_TIMEOUT_SECONDS", test_timeout,
    )

    hang_seconds = test_timeout * 10  # 500ms — way past the timeout.
    summaries = {
        "OK1": _full_summary("OK1"),
        "OK2": _full_summary("OK2"),
    }

    async def _build(dataset_id: str, *, session: Any = None) -> DatasetSummary:
        if dataset_id == "HANGS":
            # Sleep way past the timeout. asyncio.wait_for must cancel
            # this and the enricher must surface ``None`` for the row.
            await asyncio.sleep(hang_seconds)
            return _full_summary("HANGS")  # unreachable — sleep gets cancelled
        if dataset_id in summaries:
            return summaries[dataset_id]
        raise KeyError(dataset_id)

    summary_svc = Mock(spec=DatasetSummaryService)
    summary_svc.build_summary = AsyncMock(side_effect=_build)

    svc = DatasetService(Mock())

    payload: dict[str, Any] = {
        "totalNumber": 3,
        "datasets": [
            {"id": "OK1", "name": "Ok1"},
            {"id": "HANGS", "name": "Stuck"},
            {"id": "OK2", "name": "Ok2"},
        ],
    }

    t0 = time.perf_counter()
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )
    elapsed = time.perf_counter() - t0

    rows = payload["datasets"]
    # Critical: no exception bubbled up; the page rendered.
    assert rows[0]["summary"] is not None, "OK1 should have a summary"
    assert rows[1]["summary"] is None, (
        "HANGS row exceeded PER_ROW_SUMMARY_TIMEOUT_SECONDS — must be null"
    )
    assert rows[2]["summary"] is not None, "OK2 should have a summary"

    # Sanity: the timed-out row's raw cloud fields are preserved
    # (frontend still has something to render via the fallback).
    assert rows[1]["id"] == "HANGS"
    assert rows[1]["name"] == "Stuck"

    # The whole call must finish near ``test_timeout``, not ``hang_seconds``.
    # Generous ceiling at half of hang_seconds — leaves plenty of CI
    # headroom while still catching a regression where the timeout
    # didn't fire (which would push elapsed past 500ms).
    assert elapsed < hang_seconds / 2, (
        f"Enrichment took {elapsed*1000:.0f}ms; expected "
        f"<{(hang_seconds/2)*1000:.0f}ms (timeout was {test_timeout*1000:.0f}ms). "
        "asyncio.wait_for is not cancelling the stuck synth."
    )


@pytest.mark.asyncio
async def test_enricher_default_timeout_is_5_seconds() -> None:
    """Pin the default budget at 5s — generous enough for genuine cold
    builds (~3s) without burning the FastAPI retry budget on outliers.
    Changing this number is a deliberate decision; this test forces a
    review.
    """
    assert PER_ROW_SUMMARY_TIMEOUT_SECONDS == 5.0


# ---------------------------------------------------------------------------
# Short-circuit branch (ndi-cloud-node#15)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_short_circuits_when_cloud_provides_fields() -> None:
    """When the cloud-list response already contains species / brainRegions
    / numberOfSubjects (ndi-cloud-node#15), the enricher must NOT call the
    synthesizer for that row — it builds the compact summary directly.
    """
    svc, summary_svc = _mk_service_with_mock_summary({})

    payload: dict[str, Any] = {
        "totalNumber": 1,
        "datasets": [{
            "id": "SHORT",
            "name": "Short-circuited",
            "species": "Mus musculus, Rattus norvegicus",
            "brainRegions": "hippocampus, primary visual cortex",
            "numberOfSubjects": 42,
            "documentCount": 1500,
            "license": "CC-BY-4.0",
            "doi": "https://doi.org/10.63884/short",
            "createdAt": "2024-01-15T00:00:00.000Z",
        }],
    }
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )

    # Synthesizer must NOT have been called — that's the whole point.
    summary_svc.build_summary.assert_not_called()

    row = payload["datasets"][0]
    assert row["summary"] is not None
    s = row["summary"]
    assert s["datasetId"] == "SHORT"
    assert s["counts"]["subjects"] == 42
    assert s["counts"]["totalDocuments"] == 1500
    assert len(s["species"]) == 2
    assert s["species"][0]["label"] == "Mus musculus"
    assert s["species"][0]["ontologyId"] is None  # cloud doesn't send ontology IDs
    assert len(s["brainRegions"]) == 2
    assert s["citation"]["license"] == "CC-BY-4.0"
    assert s["citation"]["year"] == 2024


@pytest.mark.asyncio
async def test_short_circuit_gate_is_conservative() -> None:
    """Partial schema upgrades must NOT trigger the short-circuit — if the
    cloud only sent species but not brainRegions/numberOfSubjects we must
    still fall through to the synthesizer so the user doesn't get a
    half-empty card.
    """
    summaries = {"PARTIAL": _full_summary("PARTIAL")}
    svc, summary_svc = _mk_service_with_mock_summary(summaries)

    payload: dict[str, Any] = {
        "totalNumber": 1,
        "datasets": [{
            "id": "PARTIAL",
            "name": "Partial",
            # Only species present — missing brainRegions + numberOfSubjects.
            "species": "Mus musculus",
        }],
    }
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )

    # Short-circuit skipped → synth ran.
    summary_svc.build_summary.assert_called_once_with("PARTIAL", session=None)
    assert payload["datasets"][0]["summary"] is not None


# ---------------------------------------------------------------------------
# Edge: empty list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_handles_empty_list() -> None:
    svc, summary_svc = _mk_service_with_mock_summary({})
    payload: dict[str, Any] = {"totalNumber": 0, "datasets": []}
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )
    assert payload["datasets"] == []
    summary_svc.build_summary.assert_not_called()


# ---------------------------------------------------------------------------
# Edge: row without ID → summary: None, no crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_handles_row_without_id() -> None:
    svc, summary_svc = _mk_service_with_mock_summary({})
    payload: dict[str, Any] = {
        "totalNumber": 1,
        "datasets": [{"name": "orphan"}],  # no id
    }
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )
    assert payload["datasets"][0]["summary"] is None
    summary_svc.build_summary.assert_not_called()


# ---------------------------------------------------------------------------
# Perf smoke: 20-dataset page with Semaphore(3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_perf_smoke_20_datasets() -> None:
    """20 datasets x 50ms synth latency under Semaphore(3) should complete
    in ~ceil(20/3) * 50ms = 7 x 50 = 350ms, plus overhead. Assert < 1s to
    leave generous headroom for CI noise while still catching a serial
    regression (which would be 1000ms+).
    """
    n = 20
    summaries = {f"D{i}": _full_summary(f"D{i}") for i in range(n)}
    svc, summary_svc = _mk_service_with_mock_summary(
        summaries, per_call_delay=0.05,
    )
    payload: dict[str, Any] = {
        "totalNumber": n,
        "datasets": [{"id": f"D{i}", "name": f"n{i}"} for i in range(n)],
    }
    t0 = time.perf_counter()
    await svc._enrich_list_response(
        payload, summary_service=summary_svc, session=None,
    )
    elapsed = time.perf_counter() - t0

    # Serial would be 20 * 50 = 1000ms. With concurrency 3 we expect ~350ms.
    # Generous 1.0s ceiling to tolerate CI jitter.
    assert elapsed < 1.0, f"Enrichment took {elapsed*1000:.0f}ms (expected < 1000ms)"
    # All rows enriched.
    assert all(row["summary"] is not None for row in payload["datasets"])


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

def test_row_dataset_id_prefers_id_over_underscore_id() -> None:
    assert _row_dataset_id({"id": "A", "_id": "B"}) == "A"
    assert _row_dataset_id({"_id": "B"}) == "B"
    assert _row_dataset_id({"datasetId": "C"}) == "C"
    assert _row_dataset_id({"name": "missing id"}) is None
    assert _row_dataset_id({"id": ""}) is None  # empty string isn't valid


def test_csv_to_ontology_terms_splits_and_dedupes() -> None:
    terms = _csv_to_ontology_terms("Rattus norvegicus, Mus musculus, Rattus norvegicus")
    assert terms is not None
    assert len(terms) == 2
    assert terms[0].label == "Rattus norvegicus"
    assert terms[1].label == "Mus musculus"
    # All ontologyId values are None — cloud list doesn't carry them.
    assert all(t.ontologyId is None for t in terms)


def test_csv_to_ontology_terms_returns_none_for_missing() -> None:
    assert _csv_to_ontology_terms(None) is None
    assert _csv_to_ontology_terms(42) is None


def test_csv_to_ontology_terms_returns_empty_list_for_blank_string() -> None:
    # A string that's all whitespace / commas is distinguishable from
    # absence — "genuinely empty" rather than "not populated".
    assert _csv_to_ontology_terms("  ,  ") == []


def test_compact_summary_from_cloud_fields_requires_all_three_signals() -> None:
    # Has species + brainRegions but missing numberOfSubjects → None.
    assert _compact_summary_from_cloud_fields({
        "id": "X",
        "species": "Mouse",
        "brainRegions": "cortex",
    }) is None
    # Missing species.
    assert _compact_summary_from_cloud_fields({
        "id": "X",
        "brainRegions": "cortex",
        "numberOfSubjects": 1,
    }) is None
    # All three present → short-circuit fires.
    out = _compact_summary_from_cloud_fields({
        "id": "X",
        "name": "Dataset X",
        "species": "Mouse",
        "brainRegions": "cortex",
        "numberOfSubjects": 1,
    })
    assert isinstance(out, CompactDatasetSummary)
    assert out.datasetId == "X"


def test_compact_summary_from_full_round_trips() -> None:
    full = _full_summary("D1", subjects=5, total_documents=120)
    compact = CompactDatasetSummary.from_full(full)
    assert compact.datasetId == "D1"
    assert compact.counts.subjects == 5
    assert compact.counts.totalDocuments == 120
    assert compact.species is not None and compact.species[0].label == "Rattus norvegicus"
    assert compact.brainRegions is not None
    assert compact.citation.title == "Dataset D1"
    assert compact.citation.license == "CC-BY-4.0"
    assert compact.citation.year == 2025


def test_compact_summary_preserves_null_vs_empty_distinction() -> None:
    """`[]` and `None` carry different meaning (amendment §3). Make sure
    `from_full` preserves the null→null, []→[] mapping.
    """
    full_with_none = _full_summary("D1")
    full_with_none_species = full_with_none.model_copy(update={"species": None})
    compact = CompactDatasetSummary.from_full(full_with_none_species)
    assert compact.species is None

    full_with_empty_species = full_with_none.model_copy(update={"species": []})
    compact2 = CompactDatasetSummary.from_full(full_with_empty_species)
    assert compact2.species == []


def test_short_circuit_tolerates_malformed_created_at() -> None:
    """The cloud's ``createdAt`` should be ISO-8601 but a legacy row with
    a truncated string must not crash the enricher — ``year`` falls back
    to ``None`` rather than raising.
    """
    out = _compact_summary_from_cloud_fields({
        "id": "X",
        "name": "X",
        "species": "Mouse",
        "brainRegions": "cortex",
        "numberOfSubjects": 1,
        "createdAt": "not-an-iso-date",
    })
    assert isinstance(out, CompactDatasetSummary)
    assert out.citation.year is None


def test_short_circuit_requires_row_to_carry_id() -> None:
    """Defensive: even with species/brainRegions/numberOfSubjects present,
    a row with no ID can't produce a summary (nothing to point it at).
    """
    out = _compact_summary_from_cloud_fields({
        "id": "",
        "species": "Mouse",
        "brainRegions": "cortex",
        "numberOfSubjects": 1,
    })
    assert out is None
