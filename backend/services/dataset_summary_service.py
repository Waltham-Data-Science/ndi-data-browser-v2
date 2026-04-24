"""DatasetSummary synthesizer — Plan B B1.

Composes the four cloud primitives shipped by ndi-cloud-node PRs #9/#10/#11/#12
(indexed `classLineage`, `/document-class-counts`, CSV-scope `/ndiquery`,
bulk-fetch with 500-doc cap) into a single structured fact sheet per dataset.

Field-extraction logic is the v2 port of NDI-python's ``src/ndi/fun/doc_table.py``
paths. Reuses the Schema-A/B dispatch and ``_enriched_openminds`` pattern from
``summary_table_service`` so a refactor in one place cascades correctly to the
other.

Cache key: ``summary:v1:{dataset_id}:{user_scope}`` with a 5-minute TTL.
Freshness beats TTL economy here (amendment doc §4.B3) — a freshly published
dataset must show up on a detail page within minutes, not hours.

HTTP boundary: every cloud call routes through :mod:`backend.clients.ndi_cloud`.
No direct ``httpx``/``requests``/``aiohttp``/``urllib3`` imports — ruff's
``flake8-tidy-imports.banned-api`` gate (ADR-009) enforces this at CI.

Short-circuit note
------------------

If ndi-cloud-node #15 (``DatasetListResult`` serializer expansion) ships,
``GET /datasets/:id`` will start returning ``species``, ``brainRegions`` and
``numberOfSubjects`` pre-computed. :func:`_summary_from_cloud_fields` is
intentionally structured so a future patch can short-circuit the ndiquery
fanout when those fields are already present. Until then we compute locally.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictStr, conint

from ..auth.session import SessionData, user_scope_for
from ..cache.redis_table import RedisTableCache
from ..clients.ndi_cloud import BULK_FETCH_MAX, NdiCloudClient
from ..observability.logging import get_logger
from .ontology_service import OntologyService
from .summary_table_service import (
    _attach_openminds_enrichment,
    _clean,
    _depends_on_value_by_name,
    _extract_ids,
    _first,
    _openminds_name_and_ontology,
    _openminds_type_suffix,
)

log = get_logger(__name__)

SUMMARY_SCHEMA_VERSION = "summary:v1"
SUMMARY_CACHE_TTL_SECONDS = 5 * 60  # freshness > TTL economy (amendment §4.B3)
SUMMARY_KEY_PREFIX = "summary:v1"
# Audit 2026-04-23 (#60): bumped 3 → 6 to actually match
# ``summary_table_service.MAX_CONCURRENT_BULK_FETCH`` (the prior comment
# claimed alignment but the values differed). Catalog list-with-summary
# enrichment pages faned out under the smaller bound, serializing cold
# summaries ≈17 rounds for a 100-dataset page.
MAX_CONCURRENT_BULK_FETCH = 6


# ---------------------------------------------------------------------------
# Data-shape contracts — mirrored in frontend/src/types/dataset-summary.ts
# ---------------------------------------------------------------------------

class OntologyTerm(BaseModel):
    """Label + provider-scoped ID for a structured fact. ``ontologyId`` is
    ``None`` when the underlying document recorded a name but no ontology
    reference (e.g. Haley's GeneticStrainType with empty
    ``preferredOntologyIdentifier``).
    """

    model_config = ConfigDict(extra="forbid")

    label: StrictStr
    ontologyId: StrictStr | None = None


class DatasetSummaryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions: int = Field(ge=0)
    subjects: int = Field(ge=0)
    probes: int = Field(ge=0)
    elements: int = Field(ge=0)
    epochs: int = Field(ge=0)
    totalDocuments: int = Field(ge=0)


class DatasetSummaryDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    earliest: StrictStr | None = None
    latest: StrictStr | None = None


class DatasetSummaryContributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firstName: StrictStr
    lastName: StrictStr
    orcid: StrictStr | None = None


class DatasetSummaryCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    license: StrictStr | None = None
    datasetDoi: StrictStr | None = None
    paperDois: list[StrictStr]
    contributors: list[DatasetSummaryContributor]
    # Record-creation year from ``createdAt`` — NOT the paper publication
    # year. See ``_publication_year`` for rationale.
    year: conint(ge=1900) | None = None  # type: ignore[valid-type]


class DatasetSummary(BaseModel):
    """Structured, frontend-ready synthesis of a dataset. Empty ``[]`` means
    the fact was queried and genuinely absent; ``None`` means the extraction
    did not run (e.g. zero subjects → no species lookup).
    """

    model_config = ConfigDict(extra="forbid")

    datasetId: StrictStr
    counts: DatasetSummaryCounts
    species: list[OntologyTerm] | None = None
    strains: list[OntologyTerm] | None = None
    sexes: list[OntologyTerm] | None = None
    brainRegions: list[OntologyTerm] | None = None
    probeTypes: list[StrictStr] | None = None
    dateRange: DatasetSummaryDateRange
    totalSizeBytes: int | None = None
    citation: DatasetSummaryCitation
    computedAt: StrictStr
    schemaVersion: Literal["summary:v1"] = "summary:v1"
    extractionWarnings: list[StrictStr] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Compact catalog-card projection (Plan B B2)
# ---------------------------------------------------------------------------

class CompactDatasetSummaryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjects: int = Field(ge=0)
    totalDocuments: int = Field(ge=0)


class CompactDatasetSummaryCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    license: StrictStr | None = None
    datasetDoi: StrictStr | None = None
    year: conint(ge=1900) | None = None  # type: ignore[valid-type]


class CompactDatasetSummary(BaseModel):
    """Bytes-on-wire minimizer for catalog-card use (amendment §4.B2).

    A strict subset of :class:`DatasetSummary`: just the datasetId, the two
    counts a card displays (subjects + totalDocuments), the two multi-valued
    facts it chips (species, brainRegions), and the minimum citation header
    (title + license + DOI + upload year).

    Rationale
    ---------

    A 20-row catalog page carrying the full :class:`DatasetSummary` adds
    ~4-8 KB per row — contributors, paperDois, extractionWarnings, probeTypes,
    strains, sexes, computedAt. The card doesn't render any of those. This
    projection is ~400-600 bytes per row so a 50-row page still fits under
    ~30 KB post-gzip.

    Clients that need the full shape (dataset detail page) continue to hit
    ``GET /api/datasets/:id/summary``. This is a new, additive type — NOT a
    mutation of :class:`DatasetSummary`.
    """

    model_config = ConfigDict(extra="forbid")

    datasetId: StrictStr
    counts: CompactDatasetSummaryCounts
    species: list[OntologyTerm] | None = None
    brainRegions: list[OntologyTerm] | None = None
    citation: CompactDatasetSummaryCitation
    schemaVersion: Literal["summary:v1"] = "summary:v1"

    @classmethod
    def from_full(cls, full: DatasetSummary) -> CompactDatasetSummary:
        """Project the full :class:`DatasetSummary` down to the catalog-card
        shape. Zero-copy on the ontology-term lists (same underlying objects)
        because they're immutable from the consumer's perspective.
        """
        return cls(
            datasetId=full.datasetId,
            counts=CompactDatasetSummaryCounts(
                subjects=full.counts.subjects,
                totalDocuments=full.counts.totalDocuments,
            ),
            species=list(full.species) if full.species is not None else None,
            brainRegions=(
                list(full.brainRegions) if full.brainRegions is not None else None
            ),
            citation=CompactDatasetSummaryCitation(
                title=full.citation.title,
                license=full.citation.license,
                datasetDoi=full.citation.datasetDoi,
                year=full.citation.year,
            ),
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DatasetSummaryService:
    """Compose one :class:`DatasetSummary` per dataset. Stateless across
    requests; one service instance per :class:`NdiCloudClient` (wired in
    :mod:`backend.routers._deps`).
    """

    def __init__(
        self,
        cloud: NdiCloudClient,
        ontology: OntologyService,
        *,
        cache: RedisTableCache | None = None,
    ) -> None:
        self.cloud = cloud
        self.ontology = ontology
        self.cache = cache

    async def build_summary(
        self, dataset_id: str, *, session: SessionData | None,
    ) -> DatasetSummary:
        access_token = session.access_token if session else None
        if self.cache is not None:
            key = summary_cache_key(dataset_id, session)
            payload = await self.cache.get_or_compute(
                key,
                lambda: self._build_and_serialize(dataset_id, access_token=access_token),
            )
            return DatasetSummary.model_validate(payload)
        payload = await self._build_and_serialize(dataset_id, access_token=access_token)
        return DatasetSummary.model_validate(payload)

    async def _build_and_serialize(
        self, dataset_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        summary = await self._build(dataset_id, access_token=access_token)
        # Cache writes go through JSON, so serialize to a plain dict here.
        return summary.model_dump(mode="json")

    async def _build(
        self, dataset_id: str, *, access_token: str | None,
    ) -> DatasetSummary:
        t0 = time.perf_counter()
        warnings: list[str] = []
        sem = asyncio.Semaphore(MAX_CONCURRENT_BULK_FETCH)

        # Stage 1: the two cheap, always-needed calls. /datasets/:id can 404 —
        # let NotFound propagate unchanged through the cloud client. counts
        # is resilient against empty datasets (returns zeros).
        dataset_task = self.cloud.get_dataset(dataset_id, access_token=access_token)
        counts_task = self.cloud.get_document_class_counts(
            dataset_id, access_token=access_token,
        )
        dataset_raw, counts_raw = await asyncio.gather(dataset_task, counts_task)

        counts = _counts_from_raw(counts_raw)

        # Stage 2: fan out the per-class fetches needed for structured facts.
        # openminds_subject → species/strains/sexes; probe_location →
        # brainRegions; element (primary=probe-like) → probeTypes. These all
        # share the dataset scope so we parallelize via asyncio.gather with
        # a shared semaphore bounding bulk-fetch concurrency.
        subjects_present = counts.subjects > 0
        probe_present = counts.probes > 0 or counts.elements > 0

        if subjects_present:
            om_task = self._fetch_class(
                dataset_id, "openminds_subject",
                access_token=access_token, sem=sem,
            )
        else:
            om_task = _empty_list()

        if probe_present:
            pl_task = self._fetch_class(
                dataset_id, "probe_location",
                access_token=access_token, sem=sem,
            )
            element_task = self._fetch_class(
                dataset_id, "element",
                access_token=access_token, sem=sem,
            )
        else:
            pl_task = _empty_list()
            element_task = _empty_list()

        # Shield each leg with return_exceptions so one flaky class doesn't
        # torpedo the whole summary — we surface a warning instead.
        results = await asyncio.gather(om_task, pl_task, element_task, return_exceptions=True)
        openminds_docs = _result_or_warn(results[0], "openminds_subject", warnings)
        probe_location_docs = _result_or_warn(results[1], "probe_location", warnings)
        element_docs = _result_or_warn(results[2], "element", warnings)

        # Structured facts.
        species = _extract_om_terms(
            openminds_docs, "Species", warnings=warnings,
        ) if subjects_present else None
        strains = _extract_om_terms(
            openminds_docs, "Strain", warnings=warnings,
        ) if subjects_present else None
        sexes = _extract_om_terms(
            openminds_docs, "BiologicalSex", warnings=warnings,
        ) if subjects_present else None
        brain_regions = _extract_probe_location_terms(
            probe_location_docs, warnings=warnings,
        ) if probe_present else None
        probe_types = _extract_probe_types(element_docs) if probe_present else None

        # Ontology resolution — delegate label enrichment. Dedupe by
        # ontologyId so we don't look up the same term twice.
        await self._enrich_ontology_labels(
            [species, strains, sexes, brain_regions], warnings=warnings,
        )

        summary = DatasetSummary(
            datasetId=dataset_id,
            counts=counts,
            species=species,
            strains=strains,
            sexes=sexes,
            brainRegions=brain_regions,
            probeTypes=probe_types,
            dateRange=_date_range_from_raw(dataset_raw),
            totalSizeBytes=_size_from_raw(dataset_raw),
            citation=_citation_from_raw(dataset_raw),
            computedAt=_now_iso8601(),
            extractionWarnings=warnings,
        )
        log.info(
            "dataset_summary.build",
            dataset_id=dataset_id,
            subjects=counts.subjects,
            species=len(species) if species else 0,
            strains=len(strains) if strains else 0,
            warnings=len(warnings),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return summary

    # --- Class fanout ----------------------------------------------------

    async def _fetch_class(
        self,
        dataset_id: str,
        class_name: str,
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        body = await self.cloud.ndiquery(
            searchstructure=[{"operation": "isa", "param1": class_name}],
            scope=dataset_id,
            access_token=access_token,
        )
        ids = _extract_ids(body)
        if not ids:
            return []
        return await self._bulk_fetch_all(
            dataset_id, ids, access_token=access_token, sem=sem,
        )

    async def _bulk_fetch_all(
        self,
        dataset_id: str,
        ids: list[str],
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        batches = [
            ids[i : i + BULK_FETCH_MAX] for i in range(0, len(ids), BULK_FETCH_MAX)
        ]

        async def _one(batch: list[str]) -> list[dict[str, Any]]:
            async with sem:
                return await self.cloud.bulk_fetch(
                    dataset_id, batch, access_token=access_token,
                )

        chunks = await asyncio.gather(*[_one(b) for b in batches])
        flat: list[dict[str, Any]] = []
        for c in chunks:
            flat.extend(c)
        return flat

    # --- Ontology resolution --------------------------------------------

    async def _enrich_ontology_labels(
        self,
        term_lists: Iterable[list[OntologyTerm] | None],
        *,
        warnings: list[str],
    ) -> None:
        """In-place: upgrade each term's ``label`` to the ontology-resolver
        label where one is available. Skips terms whose provider prefix isn't
        supported. Lookup failures downgrade to the document-reported label
        (which we already have) with a warning — summaries never fail to
        render because an external ontology lookup timed out.
        """
        # Collect unique ontology IDs that parse as PROVIDER:LOCAL.
        unique_ids: dict[str, list[OntologyTerm]] = {}
        for lst in term_lists:
            if not lst:
                continue
            for term in lst:
                if term.ontologyId and ":" in term.ontologyId:
                    unique_ids.setdefault(term.ontologyId, []).append(term)
        if not unique_ids:
            return

        try:
            resolved = await self.ontology.batch_lookup(list(unique_ids.keys()))
        except Exception as e:  # pragma: no cover — batch_lookup swallows internally
            warnings.append(f"ontology batch lookup failed: {e}")
            return

        by_key = {f"{t.provider}:{t.term_id}": t for t in resolved}
        for ont_id, terms in unique_ids.items():
            hit = by_key.get(ont_id)
            if hit and hit.label:
                for term in terms:
                    # Prefer the ontology resolver's label — it's the
                    # canonical one. Keep the doc label if the resolver
                    # came back empty.
                    term.label = hit.label


# ---------------------------------------------------------------------------
# Cache key helper (public for tests)
# ---------------------------------------------------------------------------

def summary_cache_key(dataset_id: str, session: SessionData | None) -> str:
    return f"{SUMMARY_KEY_PREFIX}:{dataset_id}:{user_scope_for(session)}"


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

async def _empty_list() -> list[dict[str, Any]]:
    return []


def _result_or_warn(
    result: Any, what: str, warnings: list[str],
) -> list[dict[str, Any]]:
    if isinstance(result, BaseException):
        warnings.append(f"{what} query failed: {result!s}")
        return []
    return cast(list[dict[str, Any]], result)


def _counts_from_raw(raw: dict[str, Any]) -> DatasetSummaryCounts:
    """``/document-class-counts`` returns ``{datasetId, totalDocuments,
    classCounts: {class_name: n}}``. We map the canonical classes; any
    missing class defaults to 0 (the cloud omits classes with zero docs).
    """
    class_counts = raw.get("classCounts") or {}
    # Sessions and probes: the cloud reports whichever class name the
    # dataset used. Fall back across `probe` / `element` and `session` /
    # `session_in_a_dataset` so older and newer datasets both reconcile.
    return DatasetSummaryCounts(
        sessions=int(
            class_counts.get("session")
            or class_counts.get("session_in_a_dataset")
            or 0,
        ),
        subjects=int(class_counts.get("subject") or 0),
        probes=int(class_counts.get("probe") or 0),
        elements=int(class_counts.get("element") or 0),
        epochs=int(
            class_counts.get("element_epoch") or class_counts.get("epoch") or 0,
        ),
        totalDocuments=int(raw.get("totalDocuments") or 0),
    )


def _extract_om_terms(
    openminds_docs: list[dict[str, Any]],
    type_suffix: str,
    *,
    warnings: list[str],
) -> list[OntologyTerm]:
    """Group the openminds_subject docs by their subject-id edge, dispatch
    per-subject through :func:`_openminds_name_and_ontology`, dedupe by
    ``ontologyId`` (falling back to label).
    """
    # Group by subject.
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for doc in openminds_docs:
        sid = _depends_on_value_by_name(doc, "subject_id")
        if not sid:
            continue
        by_subject.setdefault(sid, []).append(doc)

    present_any_of_type = False
    saw_label_without_ontology = False
    terms: list[OntologyTerm] = []
    seen: dict[str, int] = {}
    for _subject_id, companions in by_subject.items():
        subject_envelope = {"_enriched_openminds": companions}
        # Presence check: does this subject have any companion of the right
        # type? Used to decide whether a Schema-B fallback should warn.
        has_match = any(
            _openminds_type_suffix(c) == type_suffix for c in companions
        )
        if not has_match:
            continue
        present_any_of_type = True
        name, ontology = _openminds_name_and_ontology(subject_envelope, type_suffix)
        if not name and not ontology:
            continue
        if name and not ontology:
            saw_label_without_ontology = True
        key = ontology or f"label::{name}"
        if key in seen:
            continue
        seen[key] = len(terms)
        terms.append(OntologyTerm(
            label=name or ontology or "unknown",
            ontologyId=ontology,
        ))

    if present_any_of_type and saw_label_without_ontology:
        warnings.append(
            f"{type_suffix.lower()} extraction: at least one subject reported "
            f"a {type_suffix} name without an ontology identifier; "
            f"fell back to label-only.",
        )

    return terms


def _extract_probe_location_terms(
    probe_location_docs: list[dict[str, Any]],
    *,
    warnings: list[str],
) -> list[OntologyTerm]:
    """``probe_location.name`` + ``ontology_name``. We split out CL (cell-type)
    ontology prefixes because those are not anatomical regions — they end up
    in the probe table's cell-type column, not the dataset brainRegions bucket.
    """
    terms: list[OntologyTerm] = []
    seen: dict[str, int] = {}
    saw_label_without_ontology = False
    for doc in probe_location_docs:
        pl = (doc.get("data") or {}).get("probe_location") or {}
        name = _clean(pl.get("name"))
        ontology = _clean(pl.get("ontology_name"))
        if ontology and isinstance(ontology, str) and ontology.upper().startswith("CL:"):
            # Cell type, not a brain region.
            continue
        if not name and not ontology:
            continue
        if name and not ontology:
            saw_label_without_ontology = True
        # Ontology IDs arrive in mixed case (e.g. ``uberon:0002436``). Normalize
        # the provider prefix to the canonical uppercase form so dedupe works
        # across the dataset.
        normalized_ontology: str | None = None
        if isinstance(ontology, str) and ":" in ontology:
            provider, _, local = ontology.partition(":")
            normalized_ontology = f"{provider.upper()}:{local}"
        elif isinstance(ontology, str):
            normalized_ontology = ontology
        key = normalized_ontology or f"label::{name}"
        if key in seen:
            continue
        seen[key] = len(terms)
        terms.append(OntologyTerm(
            label=cast(str, name or normalized_ontology or "unknown"),
            ontologyId=normalized_ontology,
        ))
    if saw_label_without_ontology:
        warnings.append(
            "brainRegions extraction: at least one probe_location had a name "
            "but no ontology_name; included as label-only.",
        )
    return terms


def _extract_probe_types(element_docs: list[dict[str, Any]]) -> list[str]:
    """The amendment doc §3 treats probeTypes as a free-text bucket: pull
    ``element.type`` / ``probe.type`` from each element doc, drop blanks,
    preserve first-seen insertion order (equal to discovery order in the
    dataset).
    """
    types: list[str] = []
    seen: set[str] = set()
    for doc in element_docs:
        # The summary_table_service helper picks the first present among
        # element.type / probe.type / element.ndi_element_class / probe.class
        # / top-level type — reuse the same priority so probeTypes matches
        # the probe table's Type column exactly.
        v = _first(
            doc,
            "element.type", "probe.type",
            "element.ndi_element_class", "probe.class", "type",
        )
        cleaned = _clean(v)
        if not isinstance(cleaned, str) or not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        types.append(cleaned)
    return types


def _date_range_from_raw(raw: dict[str, Any]) -> DatasetSummaryDateRange:
    """``GET /datasets/:id`` carries ``createdAt`` / ``updatedAt`` /
    ``uploadedAt`` per the IDataset shape. Until the cloud exposes a proper
    recording-date range we surface the record's lifecycle window. Earliest
    = createdAt, latest = updatedAt|uploadedAt (whichever is newer).
    """
    earliest = _clean(raw.get("createdAt"))
    latest_candidates = [
        _clean(raw.get("updatedAt")),
        _clean(raw.get("uploadedAt")),
    ]
    latest_candidates = [c for c in latest_candidates if isinstance(c, str)]
    latest = max(latest_candidates) if latest_candidates else None
    return DatasetSummaryDateRange(
        earliest=earliest if isinstance(earliest, str) else None,
        latest=latest,
    )


def _size_from_raw(raw: dict[str, Any]) -> int | None:
    size = raw.get("totalSize")
    if isinstance(size, int) and size >= 0:
        return size
    if isinstance(size, float) and size >= 0:
        return int(size)
    return None


def _citation_from_raw(raw: dict[str, Any]) -> DatasetSummaryCitation:
    contributors = _contributors(raw.get("contributors") or [])
    paper_dois = _paper_dois(raw.get("associatedPublications") or [])
    title = _clean(raw.get("name")) or ""
    return DatasetSummaryCitation(
        title=cast(str, title),
        license=cast("str | None", _clean(raw.get("license"))) or None,
        datasetDoi=cast("str | None", _clean(raw.get("doi"))) or None,
        paperDois=paper_dois,
        contributors=contributors,
        year=_publication_year(raw),
    )


def _contributors(items: list[Any]) -> list[DatasetSummaryContributor]:
    out: list[DatasetSummaryContributor] = []
    for c in items:
        if not isinstance(c, dict):
            continue
        first = _clean(c.get("firstName"))
        last = _clean(c.get("lastName"))
        orcid = _clean(c.get("orcid"))
        if not first and not last:
            continue
        out.append(DatasetSummaryContributor(
            firstName=cast(str, first or ""),
            lastName=cast(str, last or ""),
            orcid=cast("str | None", orcid) or None,
        ))
    return out


def _paper_dois(items: list[Any]) -> list[str]:
    dois: list[str] = []
    for p in items:
        if not isinstance(p, dict):
            continue
        doi = _clean(p.get("DOI")) or _clean(p.get("doi"))
        if isinstance(doi, str) and doi:
            dois.append(doi)
    return dois


def _publication_year(raw: dict[str, Any]) -> int | None:
    """Record-creation year pulled from ``createdAt``.

    **This is NOT the paper publication year.** The cloud does not expose
    a dedicated publication-year field on ``IDataset``. We return the year
    in which the dataset record was created in NDI Cloud — an upload /
    curation timestamp, not a research-calendar milestone. A dataset
    uploaded in 2026 that corresponds to a 2019 paper will report
    ``year=2026``.

    Consumers that need the canonical publication year should resolve it
    from ``citation.paperDois`` via an external DOI resolver (PubMed /
    Crossref). B4's cite-modal work is the natural place to render this
    field with an explicit "upload year" label.
    """
    created = _clean(raw.get("createdAt"))
    if not isinstance(created, str):
        return None
    try:
        # Tolerate `Z` suffix and offsets alike.
        parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.year


def _now_iso8601() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# Re-export helper for tests and the router.
__all__ = [
    "SUMMARY_CACHE_TTL_SECONDS",
    "SUMMARY_KEY_PREFIX",
    "SUMMARY_SCHEMA_VERSION",
    "CompactDatasetSummary",
    "CompactDatasetSummaryCitation",
    "CompactDatasetSummaryCounts",
    "DatasetSummary",
    "DatasetSummaryCitation",
    "DatasetSummaryContributor",
    "DatasetSummaryCounts",
    "DatasetSummaryDateRange",
    "DatasetSummaryService",
    "OntologyTerm",
    "summary_cache_key",
]

# Intentionally unused placeholder so the `_attach_openminds_enrichment` import
# does not get flagged by linters even when the service is extended later
# without immediately needing it.
_ = _attach_openminds_enrichment
