"""FacetService — cross-dataset distinct-value aggregator (Plan B B3).

Exercises:
  - Happy-path aggregation: 3 synthetic datasets contribute ontology-deduped
    species/brainRegions/strains/sexes + label-deduped probeTypes.
  - ``null`` summary rows (synthesizer failed) are gracefully skipped.
  - Cache miss → compute → cache hit (zero extra cloud calls on second read).
  - ``invalidate()`` clears the cache so the next read recomputes.
  - Label-dedup for ``probeTypes`` (free-text bucket).
  - Perf observation on a 50-dataset aggregation.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from backend.cache.redis_table import RedisTableCache
from backend.services.dataset_summary_service import (
    CompactDatasetSummary,
    DatasetSummary,
    DatasetSummaryCitation,
    DatasetSummaryCounts,
    DatasetSummaryDateRange,
    OntologyTerm,
)
from backend.services.facet_service import (
    FACETS_CACHE_KEY,
    FACETS_CACHE_TTL_SECONDS,
    FacetService,
    FacetsResponse,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_summary(
    dataset_id: str,
    *,
    species: list[tuple[str, str | None]] | None = None,
    strains: list[tuple[str, str | None]] | None = None,
    sexes: list[tuple[str, str | None]] | None = None,
    brain_regions: list[tuple[str, str | None]] | None = None,
    probe_types: list[str] | None = None,
) -> DatasetSummary:
    """Build a minimal :class:`DatasetSummary` for testing the facet
    aggregator. The aggregator only reads the five list fields + counts +
    datasetId, so the rest of the fields just need to satisfy Pydantic.
    """
    return DatasetSummary(
        datasetId=dataset_id,
        counts=DatasetSummaryCounts(
            sessions=0, subjects=1, probes=1, elements=1, epochs=0,
            totalDocuments=3,
        ),
        species=(
            [OntologyTerm(label=label, ontologyId=ont) for label, ont in species]
            if species is not None else None
        ),
        strains=(
            [OntologyTerm(label=label, ontologyId=ont) for label, ont in strains]
            if strains is not None else None
        ),
        sexes=(
            [OntologyTerm(label=label, ontologyId=ont) for label, ont in sexes]
            if sexes is not None else None
        ),
        brainRegions=(
            [OntologyTerm(label=label, ontologyId=ont) for label, ont in brain_regions]
            if brain_regions is not None else None
        ),
        probeTypes=probe_types,
        dateRange=DatasetSummaryDateRange(earliest=None, latest=None),
        totalSizeBytes=None,
        citation=DatasetSummaryCitation(
            title=f"Dataset {dataset_id}",
            license=None,
            datasetDoi=None,
            paperDois=[],
            contributors=[],
            year=None,
        ),
        computedAt="2026-04-17T00:00:00Z",
        extractionWarnings=[],
    )


def _make_row(dataset_id: str, compact: CompactDatasetSummary | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": dataset_id,
        "name": f"Dataset {dataset_id}",
    }
    if compact is not None:
        row["summary"] = compact.model_dump(mode="json")
    else:
        row["summary"] = None
    return row


def _fake_dataset_service(
    rows_by_page: dict[int, list[dict[str, Any]]],
    total_number: int,
) -> Any:
    """Return a stand-in for :class:`DatasetService` that feeds the facet
    aggregator a paginated published-list response. Uses ``AsyncMock`` for
    the one method the aggregator calls.
    """
    svc = AsyncMock()

    async def _list(**kwargs: Any) -> dict[str, Any]:
        page = kwargs.get("page", 1)
        return {
            "totalNumber": total_number,
            "datasets": rows_by_page.get(page, []),
        }

    svc.list_published_with_summaries = AsyncMock(side_effect=_list)
    return svc


def _fake_summary_service(
    summaries_by_id: dict[str, DatasetSummary | None],
) -> Any:
    """Stand-in for :class:`DatasetSummaryService` that returns the canned
    summary per dataset_id. Returning ``None`` from the mapping triggers
    the graceful-degrade path (logged + summary: None for that row).
    """
    svc = AsyncMock()

    async def _build(dataset_id: str, *, session: Any = None) -> DatasetSummary:
        result = summaries_by_id.get(dataset_id)
        if result is None:
            raise RuntimeError("synthesizer-failure-simulated")
        return result

    svc.build_summary = AsyncMock(side_effect=_build)
    return svc


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_aggregates_distinct_ontology_terms() -> None:
    """Three synthetic datasets share some terms, differ in others. Output
    must dedupe by ontologyId. datasetCount counts contributing rows.
    """
    ds1 = _make_summary(
        "ds1",
        species=[("Rattus norvegicus", "NCBITaxon:10116")],
        strains=[("SD", "RRID:RGD_70508")],
        sexes=[("male", "PATO:0000384")],
        brain_regions=[("bed nucleus of stria terminalis", "UBERON:0001880")],
        probe_types=["patch-Vm", "stimulator"],
    )
    ds2 = _make_summary(
        "ds2",
        species=[("Rattus norvegicus", "NCBITaxon:10116")],  # same as ds1
        strains=[("CRF-Cre", None)],  # label-only (no ontology)
        sexes=[("female", "PATO:0000383")],
        brain_regions=[("primary visual cortex", "UBERON:0002436")],
        probe_types=["patch-Vm"],  # dup of ds1
    )
    ds3 = _make_summary(
        "ds3",
        species=[("Caenorhabditis elegans", "NCBITaxon:6239")],
        strains=[("N2", "WBStrain:00000001")],
        sexes=[("hermaphrodite", "PATO:0001340")],
        brain_regions=None,  # no probes → null
        probe_types=None,
    )

    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
        _make_row("ds3", CompactDatasetSummary.from_full(ds3)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2, "ds3": ds3})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert isinstance(facets, FacetsResponse)
    assert facets.schemaVersion == "facets:v1"

    # Species: 2 distinct ontologyIds (ds1/ds2 share Rattus norvegicus).
    species_ids = {t.ontologyId for t in facets.species}
    assert species_ids == {"NCBITaxon:10116", "NCBITaxon:6239"}
    assert len(facets.species) == 2

    # Strains: 3 distinct — SD has ontologyId, CRF-Cre is label-only but
    # still dedupable, N2 has ontologyId.
    assert len(facets.strains) == 3
    assert {t.label for t in facets.strains} == {"SD", "CRF-Cre", "N2"}

    # Sexes: 3 distinct.
    assert len(facets.sexes) == 3
    assert {t.ontologyId for t in facets.sexes} == {
        "PATO:0000384", "PATO:0000383", "PATO:0001340",
    }

    # Brain regions: 2 distinct (ds3 had None).
    assert len(facets.brainRegions) == 2
    assert {t.ontologyId for t in facets.brainRegions} == {
        "UBERON:0001880", "UBERON:0002436",
    }

    # Probe types: 2 distinct (ds1 + ds2 shared patch-Vm, ds3 was None).
    assert set(facets.probeTypes) == {"patch-Vm", "stimulator"}
    assert len(facets.probeTypes) == 2

    # All 3 datasets contributed at least something.
    assert facets.datasetCount == 3


# ---------------------------------------------------------------------------
# datasetCount counts SUMMARIES AVAILABLE, not novel contributions.
#
# The reviewer flagged a subtle bug: the earlier implementation only
# incremented the counter when a dataset brought a *new* term to the
# distinct set. On a corpus where 10 datasets all report the same single
# species, that would underreport ``datasetCount == 1`` when the query-page
# header needs ``datasetCount == 10``. This test pins the correct behavior.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dataset_count_reflects_summaries_available_not_novelty() -> None:
    """Five datasets that ALL share exactly the same facet terms must still
    yield ``datasetCount == 5``, not 1. The species/strain/brain-region
    distinct lists will be size 1 (correct dedup), but the dataset counter
    should reflect how many datasets had data — not how many contributed
    something novel to the global distinct set.
    """
    shared_summary_fields = {
        "species": [("Mus musculus", "NCBITaxon:10090")],
        "strains": [("C57BL/6J", "RRID:IMSR_JAX:000664")],
        "sexes": [("male", "PATO:0000384")],
        "brain_regions": [("hippocampus", "UBERON:0002421")],
        "probe_types": ["patch-Vm"],
    }
    summaries = {
        f"ds{i}": _make_summary(f"ds{i}", **shared_summary_fields)
        for i in range(5)
    }
    rows = [
        _make_row(f"ds{i}", CompactDatasetSummary.from_full(summaries[f"ds{i}"]))
        for i in range(5)
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=5)
    sum_svc = _fake_summary_service(summaries)

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    # Distinct-value lists: all datasets share the same terms → size 1.
    assert len(facets.species) == 1
    assert len(facets.strains) == 1
    assert len(facets.sexes) == 1
    assert len(facets.brainRegions) == 1
    assert len(facets.probeTypes) == 1
    # But ALL FIVE datasets had summaries available → counter must reflect
    # that, not the "brought a novel term" reading.
    assert facets.datasetCount == 5, (
        f"datasetCount must count datasets-with-summaries-available, "
        f"NOT datasets-that-brought-a-novel-term. Got {facets.datasetCount}."
    )


# ---------------------------------------------------------------------------
# Null summary rows gracefully skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_summary_rows_gracefully_skipped() -> None:
    """A dataset whose synthesizer fails (summary=None on the row AND
    build_summary raises) must not crash aggregation. Its facts are just
    absent from the output. Other datasets still contribute normally.
    """
    ds1 = _make_summary(
        "ds1",
        species=[("Mus musculus", "NCBITaxon:10090")],
        strains=None,
        sexes=None,
        brain_regions=None,
        probe_types=["patch-Vm"],
    )
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", compact=None),  # No compact summary on row.
        _make_row("ds3", compact=None),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    # ds2 + ds3 fail synth; ds1 synthesizes fine.
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": None, "ds3": None})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    # Only ds1 contributed.
    assert {t.ontologyId for t in facets.species} == {"NCBITaxon:10090"}
    assert facets.strains == []
    assert facets.sexes == []
    assert facets.brainRegions == []
    assert facets.probeTypes == ["patch-Vm"]
    assert facets.datasetCount == 1


# ---------------------------------------------------------------------------
# Label-dedup for free-text probeTypes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_type_label_dedup() -> None:
    """probeTypes is a free-text bucket (amendment §3). The aggregator must
    dedupe by trimmed label and preserve first-seen order.
    """
    ds1 = _make_summary(
        "ds1",
        probe_types=["patch-Vm", "stimulator", "n-trode"],
    )
    ds2 = _make_summary(
        "ds2",
        probe_types=["patch-Vm", "camera"],  # dup + new
    )
    ds3 = _make_summary(
        "ds3",
        probe_types=["  stimulator  ", "EMG"],  # leading/trailing whitespace
    )

    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
        _make_row("ds3", CompactDatasetSummary.from_full(ds3)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2, "ds3": ds3})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    # First-seen order within ds1, then ds2 new, then ds3 new. Whitespace
    # trim means "  stimulator  " dedupes against ds1's "stimulator".
    assert facets.probeTypes == ["patch-Vm", "stimulator", "n-trode", "camera", "EMG"]


# ---------------------------------------------------------------------------
# Cache miss → compute → hit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_miss_then_hit_skips_cloud_on_repeat() -> None:
    """Once the aggregator fills the cache, a second ``build_facets()`` call
    within the TTL must NOT call the dataset-service-mock again.
    """
    ds1 = _make_summary(
        "ds1",
        species=[("Rattus norvegicus", "NCBITaxon:10116")],
        strains=[("SD", "RRID:RGD_70508")],
    )
    rows = [_make_row("ds1", CompactDatasetSummary.from_full(ds1))]
    ds_svc = _fake_dataset_service({1: rows}, total_number=1)
    sum_svc = _fake_summary_service({"ds1": ds1})

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = RedisTableCache(redis=redis, ttl_seconds=FACETS_CACHE_TTL_SECONDS)
    try:
        svc = FacetService(ds_svc, sum_svc, cache=cache)

        first = await svc.build_facets()
        calls_after_first = ds_svc.list_published_with_summaries.call_count
        summary_calls_after_first = sum_svc.build_summary.call_count

        second = await svc.build_facets()

        # No additional cloud calls on second read.
        assert (
            ds_svc.list_published_with_summaries.call_count
            == calls_after_first
        ), "catalog list must not be re-walked on cache hit"
        assert (
            sum_svc.build_summary.call_count == summary_calls_after_first
        ), "per-dataset summaries must not be re-fetched on cache hit"

        # Blob is byte-identical between reads.
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
    finally:
        await redis.aclose()


# ---------------------------------------------------------------------------
# invalidate() clears the cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_clears_cache_so_next_read_recomputes() -> None:
    ds1 = _make_summary(
        "ds1",
        species=[("Rattus norvegicus", "NCBITaxon:10116")],
    )
    rows = [_make_row("ds1", CompactDatasetSummary.from_full(ds1))]
    ds_svc = _fake_dataset_service({1: rows}, total_number=1)
    sum_svc = _fake_summary_service({"ds1": ds1})

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = RedisTableCache(redis=redis, ttl_seconds=FACETS_CACHE_TTL_SECONDS)
    try:
        svc = FacetService(ds_svc, sum_svc, cache=cache)
        await svc.build_facets()
        assert await redis.get(FACETS_CACHE_KEY) is not None

        await svc.invalidate()
        assert await redis.get(FACETS_CACHE_KEY) is None

        # Next read re-populates.
        await svc.build_facets()
        assert await redis.get(FACETS_CACHE_KEY) is not None
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_invalidate_is_safe_when_no_cache_configured() -> None:
    """FacetService supports operating without a cache (single-process).
    ``invalidate()`` must be a no-op in that mode, not raise.
    """
    ds_svc = _fake_dataset_service({1: []}, total_number=0)
    sum_svc = _fake_summary_service({})
    svc = FacetService(ds_svc, sum_svc, cache=None)
    # Must not raise.
    await svc.invalidate()


# ---------------------------------------------------------------------------
# Schema version is a literal (pydantic extra="forbid")
# ---------------------------------------------------------------------------

def test_facets_response_schema_version_is_literal() -> None:
    with pytest.raises(Exception):  # noqa: B017 — any pydantic ValidationError variant
        FacetsResponse.model_validate({
            "species": [],
            "brainRegions": [],
            "strains": [],
            "sexes": [],
            "probeTypes": [],
            "datasetCount": 0,
            "computedAt": "2026-04-17T00:00:00Z",
            "schemaVersion": "facets:v99",  # wrong
        })


def test_facets_response_rejects_extra_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 — any pydantic ValidationError variant
        FacetsResponse.model_validate({
            "species": [],
            "brainRegions": [],
            "strains": [],
            "sexes": [],
            "probeTypes": [],
            "datasetCount": 0,
            "computedAt": "2026-04-17T00:00:00Z",
            "schemaVersion": "facets:v1",
            "extra": "bad",
        })


# ---------------------------------------------------------------------------
# Pagination walk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_walks_multiple_pages() -> None:
    """The catalog walker must pull every page until the response is short
    (fewer rows than ``page_size``) OR total count is reached.
    """
    # Page 1 has 100 rows, page 2 has 5 rows — walker stops on page 2.
    page1 = [
        _make_row(
            f"ds{i}",
            CompactDatasetSummary.from_full(_make_summary(
                f"ds{i}",
                species=[(f"Species{i}", f"NCBITaxon:{i}")],
            )),
        )
        for i in range(100)
    ]
    page2 = [
        _make_row(
            f"ds{i}",
            CompactDatasetSummary.from_full(_make_summary(
                f"ds{i}",
                species=[(f"Species{i}", f"NCBITaxon:{i}")],
            )),
        )
        for i in range(100, 105)
    ]
    ds_svc = _fake_dataset_service({1: page1, 2: page2}, total_number=105)
    summaries = {
        f"ds{i}": _make_summary(
            f"ds{i}",
            species=[(f"Species{i}", f"NCBITaxon:{i}")],
        )
        for i in range(105)
    }
    sum_svc = _fake_summary_service(summaries)

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    # 105 distinct species aggregated.
    assert len(facets.species) == 105
    assert facets.datasetCount == 105
    # Catalog walker called exactly 2 pages.
    assert ds_svc.list_published_with_summaries.call_count == 2


# ---------------------------------------------------------------------------
# Perf observation — 50-dataset aggregation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_perf_50_dataset_aggregation_under_one_second() -> None:
    """The aggregator for 50 datasets (each with 1-2 facts per list)
    should complete in well under one second in-process. Real-world cost
    is dominated by the catalog-walk + per-dataset summaries; the mocks
    here exercise the dedupe logic + async orchestration path. This test
    is a soft-floor that surfaces if the dedupe algorithm regresses to
    quadratic behavior.
    """
    summaries = {}
    rows = []
    for i in range(50):
        s = _make_summary(
            f"ds{i}",
            species=[(f"Species{i}", f"NCBITaxon:{i}")],
            strains=[(f"Strain{i}", None)],
            sexes=[("male", "PATO:0000384")],  # all share this
            brain_regions=[(f"Region{i}", f"UBERON:{i:07d}")],
            probe_types=[f"probe-{i}", "shared-probe"],
        )
        summaries[f"ds{i}"] = s
        rows.append(_make_row(f"ds{i}", CompactDatasetSummary.from_full(s)))

    ds_svc = _fake_dataset_service({1: rows}, total_number=50)
    sum_svc = _fake_summary_service(summaries)

    svc = FacetService(ds_svc, sum_svc)
    t0 = time.perf_counter()
    facets = await svc.build_facets()
    elapsed = time.perf_counter() - t0

    # Algorithmic soft-floor: 1 second is generous for 50 in-process mocks.
    # Typical on a dev laptop ≈ 20-50 ms. If this blows up, dedupe went
    # quadratic or async orchestration regressed.
    assert elapsed < 1.0, f"50-dataset aggregation took {elapsed:.2f}s"

    assert len(facets.species) == 50
    assert len(facets.strains) == 50
    assert len(facets.sexes) == 1  # all datasets shared "male"
    assert len(facets.brainRegions) == 50
    assert len(facets.probeTypes) == 51  # 50 unique + shared-probe
    assert facets.datasetCount == 50


# ---------------------------------------------------------------------------
# ontologyId dedupe across label variation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ontology_dedup_survives_label_drift() -> None:
    """If two datasets record the same ontologyId but slightly different
    labels (human transcription, provider label change), dedup must still
    collapse them. We keep the first-seen label.
    """
    ds1 = _make_summary(
        "ds1",
        species=[("Rattus norvegicus", "NCBITaxon:10116")],
    )
    ds2 = _make_summary(
        "ds2",
        species=[("Norway rat", "NCBITaxon:10116")],  # same ID, different label
    )
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert len(facets.species) == 1
    assert facets.species[0].ontologyId == "NCBITaxon:10116"
    # First-seen label is preserved.
    assert facets.species[0].label == "Rattus norvegicus"
