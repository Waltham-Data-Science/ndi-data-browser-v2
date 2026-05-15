"""FacetService dedupe + license-normalization regression tests.

Production smoke (2026-04-26) surfaced three classes of facet duplicates:

1. **Case-identical species duplicates** — ``Caenorhabditis elegans``
   appearing twice on the chip rail because two contributing datasets
   each reported it without an ontologyId, and the legacy dedupe key
   ``label::<exact>`` happened to differ on trailing whitespace or the
   compact-vs-full extraction path.
2. **Brain-region "the / no-the" near-duplicates** —
   ``Bed nucleus of the stria terminalis (BNST)`` +
   ``Bed nucleus of stria terminalis (BNST)``. Same biological entity,
   different wording. Resolves on the parenthesized abbreviation.
3. **Mixed license formats** — ``CC-BY-4.0``, ``Creative Commons
   Attribution 4.0 International``, and ``ccByNcSa4_0`` all surfacing
   independently. Resolves on the LICENSE_LABELS normalization table.

These tests pin the post-fix behavior so the duplicates never re-surface.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.dataset_summary_service import (
    CompactDatasetSummary,
    DatasetSummary,
    DatasetSummaryCitation,
    DatasetSummaryCounts,
    DatasetSummaryDateRange,
    OntologyTerm,
)
from backend.services.facet_service import (
    LICENSE_LABELS,
    FacetService,
)

# ---------------------------------------------------------------------------
# Test-fixture helpers (mirrored from test_facet_service for self-containment)
# ---------------------------------------------------------------------------

def _make_summary(
    dataset_id: str,
    *,
    species: list[tuple[str, str | None]] | None = None,
    strains: list[tuple[str, str | None]] | None = None,
    sexes: list[tuple[str, str | None]] | None = None,
    brain_regions: list[tuple[str, str | None]] | None = None,
    probe_types: list[str] | None = None,
    license_label: str | None = None,
) -> DatasetSummary:
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
            license=license_label,
            datasetDoi=None,
            paperDois=[],
            contributors=[],
            year=None,
        ),
        computedAt="2026-04-26T00:00:00Z",
        extractionWarnings=[],
    )


def _make_row(dataset_id: str, compact: CompactDatasetSummary | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": dataset_id,
        "name": f"Dataset {dataset_id}",
    }
    row["summary"] = compact.model_dump(mode="json") if compact else None
    return row


def _fake_dataset_service(
    rows_by_page: dict[int, list[dict[str, Any]]],
    total_number: int,
) -> Any:
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
    svc = AsyncMock()

    async def _build(dataset_id: str, *, session: Any = None) -> DatasetSummary:
        result = summaries_by_id.get(dataset_id)
        if result is None:
            raise RuntimeError("synthesizer-failure-simulated")
        return result

    svc.build_summary = AsyncMock(side_effect=_build)
    return svc


# ---------------------------------------------------------------------------
# Bug 1 — case-identical species duplicates collapse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_case_identical_species_collapse_to_one_entry() -> None:
    """Two datasets each report ``Caenorhabditis elegans`` with no
    ontologyId. The legacy dedupe key ``label::<exact>`` would have
    surfaced both; the normalized key collapses them.
    """
    ds1 = _make_summary("ds1", species=[("Caenorhabditis elegans", None)])
    ds2 = _make_summary("ds2", species=[("Caenorhabditis elegans", None)])
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    species_labels = [t.label for t in facets.species]
    assert species_labels == ["Caenorhabditis elegans"], (
        f"Two case-identical species must collapse to ONE entry. "
        f"Got: {species_labels}"
    )


@pytest.mark.asyncio
async def test_labeled_and_unlabeled_species_with_same_label_merge() -> None:
    """Visual-UX audit row #6 / a395 (2026-05-12): ``/datasets`` and
    ``/query`` showed two ``Caenorhabditis elegans`` chips because one
    contributing dataset reported the species with
    ``ontologyId=NCBITaxon:6239`` and another reported it with
    ``ontologyId=None``. Pre-fix the two dedupe keys (``oid::NCBITaxon:6239``
    and ``norm::caenorhabditis elegans``) were disjoint so both
    surfaced. Post-fix the asymmetric label-alias merge collapses
    them into one chip; the merged entry keeps the ontologyId.
    """
    ds1 = _make_summary(
        "ds1", species=[("Caenorhabditis elegans", "NCBITaxon:6239")],
    )
    ds2 = _make_summary(
        "ds2", species=[("Caenorhabditis elegans", None)],
    )
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert len(facets.species) == 1, (
        f"Labeled + unlabeled same-name species must merge. Got: "
        f"{[(t.label, t.ontologyId) for t in facets.species]}"
    )
    assert facets.species[0].label == "Caenorhabditis elegans"
    # The ontologyId from the labeled side wins — more authoritative.
    assert facets.species[0].ontologyId == "NCBITaxon:6239"


@pytest.mark.asyncio
async def test_unlabeled_then_labeled_species_merge_promotes_ontology_id() -> None:
    """Reverse-order variant of the audit bug: the unlabeled entry
    arrives first, then the labeled one. The merge must still collapse
    them and promote the ontologyId onto the surviving entry."""
    ds1 = _make_summary(
        "ds1", species=[("Caenorhabditis elegans", None)],
    )
    ds2 = _make_summary(
        "ds2", species=[("Caenorhabditis elegans", "NCBITaxon:6239")],
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
    assert facets.species[0].label == "Caenorhabditis elegans"
    assert facets.species[0].ontologyId == "NCBITaxon:6239"


@pytest.mark.asyncio
async def test_whitespace_drift_in_species_collapses() -> None:
    """Trivial whitespace differences (trailing space, internal
    double-space) must dedupe — these are NOT distinct species, just
    transcription noise."""
    ds1 = _make_summary("ds1", species=[("Mus musculus", None)])
    ds2 = _make_summary("ds2", species=[("Mus  musculus ", None)])  # double-space + trailing
    ds3 = _make_summary("ds3", species=[(" Mus musculus", None)])   # leading space
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
        _make_row("ds3", CompactDatasetSummary.from_full(ds3)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2, "ds3": ds3})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert len(facets.species) == 1
    # Displayed label is the most-frequently-seen casing.
    # All three are different cased strings (count 1 each); the
    # first-seen wins on ties.
    assert facets.species[0].label == "Mus musculus"


@pytest.mark.asyncio
async def test_higher_count_casing_wins_displayed_label() -> None:
    """When two casings of the same species are seen, the more-frequent
    casing becomes the displayed label. ``Macaca mulatta`` (count 2)
    should win over ``MACACA MULATTA`` (count 1)."""
    ds1 = _make_summary("ds1", species=[("MACACA MULATTA", None)])
    ds2 = _make_summary("ds2", species=[("Macaca mulatta", None)])
    ds3 = _make_summary("ds3", species=[("Macaca mulatta", None)])
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
        _make_row("ds3", CompactDatasetSummary.from_full(ds3)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2, "ds3": ds3})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert len(facets.species) == 1
    assert facets.species[0].label == "Macaca mulatta", (
        "More-frequently-seen casing must win the displayed label."
    )


# ---------------------------------------------------------------------------
# Bug 2 — brain-region "the / no-the" near-duplicates collapse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_brain_region_paren_abbrev_collapses_near_duplicates() -> None:
    """Two datasets report the same biological region with slightly
    different wording (one with "the", one without) but the SAME
    parenthesized abbreviation. The abbrev collapses them; counts sum
    so the more-frequent wording wins display."""
    ds1 = _make_summary(
        "ds1",
        brain_regions=[("Bed nucleus of the stria terminalis (BNST)", None)],
    )
    ds2 = _make_summary(
        "ds2",
        brain_regions=[("Bed nucleus of stria terminalis (BNST)", None)],
    )
    ds3 = _make_summary(
        "ds3",
        brain_regions=[("Bed nucleus of stria terminalis (BNST)", None)],
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

    assert len(facets.brainRegions) == 1, (
        f"BNST near-duplicates must collapse on the parenthesized abbreviation. "
        f"Got: {[t.label for t in facets.brainRegions]}"
    )
    # ds2 + ds3 both used the no-"the" wording (count 2), ds1 the
    # "the" wording (count 1). Higher-count wording wins display.
    assert facets.brainRegions[0].label == "Bed nucleus of stria terminalis (BNST)"


@pytest.mark.asyncio
async def test_paren_abbrev_only_applies_to_brain_regions() -> None:
    """Species with parenthesized text in the label must NOT collapse
    on that text — paren-abbrev dedupe is brain-region-specific.
    ``Mus musculus (lab strain)`` and ``Rattus norvegicus (lab strain)``
    are different species; the shared "(lab strain)" must not merge them.
    """
    ds1 = _make_summary("ds1", species=[("Mus musculus (lab strain)", None)])
    ds2 = _make_summary("ds2", species=[("Rattus norvegicus (lab strain)", None)])
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert len(facets.species) == 2
    assert {t.label for t in facets.species} == {
        "Mus musculus (lab strain)", "Rattus norvegicus (lab strain)",
    }


@pytest.mark.asyncio
async def test_brain_region_multiple_parens_no_false_collapse() -> None:
    """When a label has more than one parenthesized abbreviation, the
    extraction is ambiguous — fall back to the normalized-label key so
    we don't accidentally collapse unrelated regions that happen to
    share an abbrev in a multi-paren label."""
    ds1 = _make_summary(
        "ds1",
        brain_regions=[("Anterior cingulate cortex (ACC) (sub-area 1)", None)],
    )
    ds2 = _make_summary(
        "ds2",
        brain_regions=[("Posterior parietal cortex (PPC) (sub-area 1)", None)],
    )
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    # Both have two parens — abbrev extraction returns None for both,
    # falls through to normalized-label key, which differs for these
    # two distinct regions. Must NOT collapse to one.
    assert len(facets.brainRegions) == 2


@pytest.mark.asyncio
async def test_ontology_id_still_takes_priority_over_label_normalization() -> None:
    """When ontologyId is present it remains the authoritative dedupe
    key — different ontologyIds must NOT merge even if labels normalize
    the same. Two datasets reporting "Hippocampus" with different
    ontology providers should stay distinct."""
    ds1 = _make_summary(
        "ds1",
        brain_regions=[("Hippocampus", "UBERON:0002421")],
    )
    ds2 = _make_summary(
        "ds2",
        brain_regions=[("Hippocampus", "ALLEN:00012345")],  # distinct provider id
    )
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert len(facets.brainRegions) == 2
    assert {t.ontologyId for t in facets.brainRegions} == {
        "UBERON:0002421", "ALLEN:00012345",
    }


# ---------------------------------------------------------------------------
# Bug 3 — license format normalization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_license_formats_collapse_to_one_canonical() -> None:
    """The three on-the-wire formats for the same logical license must
    collapse to one canonical chip:
      - ``CC-BY-4.0`` (SPDX-style short)
      - ``Creative Commons Attribution 4.0 International`` (full name)
      - ``ccBy4_0`` (camelCase enum-token leak)
    All three → ``CC BY 4.0``.
    """
    ds1 = _make_summary("ds1", license_label="CC-BY-4.0")
    ds2 = _make_summary(
        "ds2",
        license_label="Creative Commons Attribution 4.0 International",
    )
    ds3 = _make_summary("ds3", license_label="ccBy4_0")
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
        _make_row("ds3", CompactDatasetSummary.from_full(ds3)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2, "ds3": ds3})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert facets.licenses == ["CC BY 4.0"]


@pytest.mark.asyncio
async def test_camelcase_enum_token_leak_normalized_to_human_label() -> None:
    """The smoke-test caught ``ccByNcSa4_0`` leaking through as a chip.
    Must surface as ``CC BY-NC-SA 4.0`` instead.
    """
    ds = _make_summary("ds", license_label="ccByNcSa4_0")
    rows = [_make_row("ds", CompactDatasetSummary.from_full(ds))]
    ds_svc = _fake_dataset_service({1: rows}, total_number=1)
    sum_svc = _fake_summary_service({"ds": ds})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert facets.licenses == ["CC BY-NC-SA 4.0"]


@pytest.mark.asyncio
async def test_different_licenses_stay_distinct() -> None:
    """Distinct logical licenses must not collapse — only format
    variants of the SAME logical license collapse."""
    ds1 = _make_summary("ds1", license_label="CC-BY-4.0")
    ds2 = _make_summary("ds2", license_label="CC-BY-NC-4.0")
    ds3 = _make_summary("ds3", license_label="ccZero1_0")
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
        _make_row("ds3", CompactDatasetSummary.from_full(ds3)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=3)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2, "ds3": ds3})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert set(facets.licenses) == {"CC BY 4.0", "CC BY-NC 4.0", "CC0 1.0"}
    assert len(facets.licenses) == 3


@pytest.mark.asyncio
async def test_unknown_license_passes_through_trimmed() -> None:
    """A license value not in the canonicalization table must still
    surface (trimmed) rather than be silently dropped — this is a
    forward-compat hatch for novel cloud-side enum values."""
    ds = _make_summary("ds", license_label="  Some New License Variant 2.0  ")
    rows = [_make_row("ds", CompactDatasetSummary.from_full(ds))]
    ds_svc = _fake_dataset_service({1: rows}, total_number=1)
    sum_svc = _fake_summary_service({"ds": ds})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert facets.licenses == ["Some New License Variant 2.0"]


@pytest.mark.asyncio
async def test_no_license_means_empty_list() -> None:
    """Datasets without a license must not contribute a chip (no
    ``"None"`` or empty-string entry)."""
    ds1 = _make_summary("ds1", license_label=None)
    ds2 = _make_summary("ds2", license_label="")  # empty string, also a no-op
    rows = [
        _make_row("ds1", CompactDatasetSummary.from_full(ds1)),
        _make_row("ds2", CompactDatasetSummary.from_full(ds2)),
    ]
    ds_svc = _fake_dataset_service({1: rows}, total_number=2)
    sum_svc = _fake_summary_service({"ds1": ds1, "ds2": ds2})

    svc = FacetService(ds_svc, sum_svc)
    facets = await svc.build_facets()

    assert facets.licenses == []


# ---------------------------------------------------------------------------
# LICENSE_LABELS table coverage
# ---------------------------------------------------------------------------

def test_license_labels_table_covers_canonical_set() -> None:
    """Spot-check the canonical labels in the dedupe table — every
    smoke-test case + the common Creative Commons variants must be
    present so unknown-license fall-through stays the exception."""
    canonicals = set(LICENSE_LABELS.values())
    expected_canonicals = {
        "CC BY 4.0",
        "CC BY-SA 4.0",
        "CC BY-NC 4.0",
        "CC BY-NC-SA 4.0",
        "CC BY-ND 4.0",
        "CC BY-NC-ND 4.0",
        "CC0 1.0",
    }
    missing = expected_canonicals - canonicals
    assert not missing, f"LICENSE_LABELS missing canonicals: {missing}"
