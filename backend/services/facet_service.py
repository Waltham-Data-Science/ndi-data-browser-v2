"""Cross-dataset facet aggregator — Plan B B3.

Aggregates distinct structured facts (species / brain regions / strains /
sexes / probe types) across **all published datasets** into one
:class:`FacetsResponse` the query page uses to populate filter chips.

The primitives are already there:

- :meth:`DatasetService.list_published_with_summaries` walks the catalog in
  pages and embeds a :class:`CompactDatasetSummary` per row (B2's enricher,
  ADR-010-012 pipeline).
- That compact summary carries ``species`` and ``brainRegions`` directly.
  For the remaining facets (``strains``, ``sexes``, ``probeTypes``) we
  fetch the full :class:`DatasetSummary` per row via
  :meth:`DatasetSummaryService.build_summary`, which sits behind the same
  5-minute TTL cache so repeat facet computes inside one TTL window pay no
  extra cloud cost.

Dedup strategy
--------------

- Ontology-typed facets (species/brainRegions/strains/sexes) dedupe by
  ``OntologyTerm.ontologyId``. When the ontology ID is ``None`` we fall
  back to the label string so label-only terms (Haley's
  ``GeneticStrainType``) still collapse cross-dataset duplicates.
- ``probeTypes`` is a free-text bucket (amendment §3). We dedupe by the
  trimmed label.

Cache strategy (amendment §4.B3 — CRITICAL)
-------------------------------------------

The original Plan B synthesis said "cache aggressively with 1h TTL." The
amendment rewrote this: **freshness > TTL economy.** A dataset published at
T=0 should appear on facet chips within minutes, not hours.

- Primary strategy (FUTURE): invalidate on dataset-publish events. No such
  notification hook exists today — the cloud does not push publish webhooks
  to the proxy, and no polling-delta mechanism is in place either. The
  :meth:`FacetService.invalidate` method exists as the invalidation hook;
  callers can wire it into a publish-notification path when one ships. Until
  then it is dormant.
- Fallback (CURRENT): short TTL (5 minutes) + background recompute on
  read-after-TTL via the shared :class:`RedisTableCache.get_or_compute`
  primitive. Freshness lag is bounded to ``FACETS_CACHE_TTL_SECONDS``.

See ADR-013 for the full reasoning; see amendment §4.B3 for the wording
override.

Cache key: ``facets:v1`` (no per-user scope — facets are strictly public
data aggregated from published datasets. Two users reading the query page
share the same cached blob).

HTTP boundary: every cloud call routes through
:mod:`backend.clients.ndi_cloud` via the composed services. No direct
``httpx`` / ``requests`` / ``aiohttp`` / ``urllib3`` imports — ADR-009.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, conint

from ..cache.redis_table import RedisTableCache
from ..observability.logging import get_logger
from .dataset_service import DatasetService
from .dataset_summary_service import (
    CompactDatasetSummary,
    DatasetSummaryService,
    OntologyTerm,
)

log = get_logger(__name__)

FACETS_SCHEMA_VERSION = "facets:v1"
FACETS_CACHE_TTL_SECONDS = 5 * 60  # freshness > TTL economy (amendment §4.B3)
FACETS_KEY_PREFIX = "facets:v1"
FACETS_CACHE_KEY = "facets:v1"  # public data — no per-user scope

# Pagination bound for catalog walk. The cloud's per-page cap is 100; we
# request the maximum to minimize round-trips. Safety cap on total pages
# protects against a pathological catalog growth (the proxy scanning every
# published dataset is O(N_datasets) and should stay bounded).
_FACET_PAGE_SIZE = 100
_FACET_MAX_PAGES = 50  # 5000 published datasets — well above realistic corpus


# ---------------------------------------------------------------------------
# Data-shape contract — mirrored in frontend/src/types/facets.ts
# ---------------------------------------------------------------------------

class FacetsResponse(BaseModel):
    """Distinct-value facets aggregated across all published datasets.

    Empty list for any facet means the aggregation ran and found no values.
    ``datasetCount`` is the number of datasets that contributed at least one
    non-null summary; datasets with ``summary: null`` (synthesizer failed or
    short-circuit miss) are still walked but contribute nothing.
    """

    model_config = ConfigDict(extra="forbid")

    species: list[OntologyTerm] = Field(default_factory=list)
    brainRegions: list[OntologyTerm] = Field(default_factory=list)
    strains: list[OntologyTerm] = Field(default_factory=list)
    sexes: list[OntologyTerm] = Field(default_factory=list)
    probeTypes: list[StrictStr] = Field(default_factory=list)
    datasetCount: conint(ge=0) = 0  # type: ignore[valid-type]
    computedAt: StrictStr
    schemaVersion: Literal["facets:v1"] = "facets:v1"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FacetService:
    """Build one :class:`FacetsResponse` from the published catalog.

    Stateless across requests; one service instance per
    :class:`NdiCloudClient` (wired in :mod:`backend.routers._deps`).
    """

    def __init__(
        self,
        dataset_service: DatasetService,
        summary_service: DatasetSummaryService,
        *,
        cache: RedisTableCache | None = None,
    ) -> None:
        self.dataset_service = dataset_service
        self.summary_service = summary_service
        self.cache = cache

    async def build_facets(self) -> FacetsResponse:
        """Return distinct-value facets across all published datasets.

        Cached under :data:`FACETS_CACHE_KEY` with
        :data:`FACETS_CACHE_TTL_SECONDS` TTL. A cache miss aggregates the
        current published catalog; a hit returns the prior blob verbatim.
        """
        if self.cache is not None:
            payload = await self.cache.get_or_compute(
                FACETS_CACHE_KEY,
                self._build_and_serialize,
            )
            return FacetsResponse.model_validate(payload)
        payload = await self._build_and_serialize()
        return FacetsResponse.model_validate(payload)

    async def invalidate(self) -> None:
        """Flush the facets cache.

        Invocation point for the "invalidate on dataset-publish" strategy
        (amendment §4.B3). No such notification hook exists today — this is
        the method future wiring (webhook, polling delta) should call. Safe
        to call when no cache is configured (it becomes a no-op).
        """
        if self.cache is None:
            return
        await self.cache.invalidate(FACETS_CACHE_KEY)

    async def _build_and_serialize(self) -> dict[str, Any]:
        facets = await self._build()
        return facets.model_dump(mode="json")

    async def _build(self) -> FacetsResponse:
        t0 = time.perf_counter()

        # Walk the published catalog in pages. Each page carries a
        # CompactDatasetSummary per row (via the existing B2 enricher).
        rows = await self._collect_published_rows()
        # Fetch full summaries for rows that have a dataset ID. Use the
        # service's cache (5-minute TTL) so repeat facet builds amortize.
        full_summaries = await self._collect_full_summaries(rows)

        accumulator = _FacetAccumulator()
        for row, summary in zip(rows, full_summaries, strict=True):
            accumulator.add_row(row, summary)

        response = accumulator.to_response()
        log.info(
            "facet_service.build",
            datasets_walked=len(rows),
            datasets_contributed=response.datasetCount,
            species=len(response.species),
            brain_regions=len(response.brainRegions),
            strains=len(response.strains),
            sexes=len(response.sexes),
            probe_types=len(response.probeTypes),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return response

    async def _collect_published_rows(self) -> list[dict[str, Any]]:
        """Walk the published catalog paginating at 100/page until exhausted.

        Calls :meth:`DatasetService.list_published_with_summaries` which
        already embeds compact summaries per row. Facets are public data;
        ``session=None`` is deliberate.
        """
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= _FACET_MAX_PAGES:
            payload = await self.dataset_service.list_published_with_summaries(
                page=page,
                page_size=_FACET_PAGE_SIZE,
                summary_service=self.summary_service,
                session=None,
            )
            chunk = payload.get("datasets")
            if not isinstance(chunk, list):
                break
            rows.extend(c for c in chunk if isinstance(c, dict))
            total = payload.get("totalNumber")
            if (
                len(chunk) < _FACET_PAGE_SIZE
                or (isinstance(total, int) and len(rows) >= total)
            ):
                break
            page += 1
        return rows

    async def _collect_full_summaries(
        self, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any] | None]:
        """Fetch the full :class:`DatasetSummary` for each row, serialized
        to ``dict`` form. Returns ``None`` for rows where the synthesizer
        fails (same graceful-degrade contract as catalog-summary enrichment).

        Bounded by :data:`MAX_CONCURRENT_SUMMARIES` via the summary service's
        own semaphore — we rely on its cache hit rate for the per-dataset
        calls. ``session=None`` because facets aggregate public data.
        """
        sem = asyncio.Semaphore(3)

        async def _one(row: dict[str, Any]) -> dict[str, Any] | None:
            dataset_id = _row_dataset_id(row)
            if not dataset_id:
                return None
            async with sem:
                try:
                    summary = await self.summary_service.build_summary(
                        dataset_id, session=None,
                    )
                except Exception as e:
                    log.warning(
                        "facet_service.summary_failed",
                        dataset_id=dataset_id,
                        error=str(e),
                    )
                    return None
            return summary.model_dump(mode="json")

        return list(await asyncio.gather(*[_one(r) for r in rows]))


# ---------------------------------------------------------------------------
# Accumulator — single-row ingest with contribution bookkeeping
# ---------------------------------------------------------------------------

class _FacetAccumulator:
    """Row-by-row state machine that tracks distinct facet values + counts
    how many datasets had a non-null summary available.

    Pulled out of :meth:`FacetService._build` so the branching per-row
    dispatch is contained in one object with small methods. Pure in-memory;
    no cloud calls.

    ``contributing_datasets`` counts datasets whose compact OR full summary
    was available (not ``None``) — NOT datasets that brought a novel term
    to the distinct set. The distinction matters: on a corpus where 10
    datasets all report the same single species, the "10 datasets contributed
    data" reading matches user expectation for the query-page header; a
    "1 dataset contributed a novel term" reading would underreport by 9x.
    """

    def __init__(self) -> None:
        self.species: list[OntologyTerm] = []
        self.brain_regions: list[OntologyTerm] = []
        self.strains: list[OntologyTerm] = []
        self.sexes: list[OntologyTerm] = []
        self.probe_types: list[str] = []

        self._species_seen: dict[str, int] = {}
        self._brain_regions_seen: dict[str, int] = {}
        self._strains_seen: dict[str, int] = {}
        self._sexes_seen: dict[str, int] = {}
        self._probe_types_seen: set[str] = set()

        self.contributing_datasets = 0

    def add_row(
        self,
        row: dict[str, Any],
        summary: dict[str, Any] | None,
    ) -> None:
        """Ingest one (row, summary) pair. ``row`` comes from the published
        catalog; ``summary`` is the full :class:`DatasetSummary` serialized
        dict (or ``None`` when the synthesizer failed).

        ``contributing_datasets`` increments once per dataset that had ANY
        usable summary payload (compact OR full), regardless of whether the
        dataset brought a novel term to the distinct set. See class
        docstring for the rationale.
        """
        dataset_id = _row_dataset_id(row)
        if not dataset_id:
            return
        compact = _compact_from_row(row)
        # Ingestion flags feed the distinct-set, but dataset counting
        # happens on summary availability, not on novelty.
        self._ingest_from_compact_or_summary(compact, summary)
        if summary is not None:
            self._ingest_from_full_summary(summary)
        if compact is not None or summary is not None:
            self.contributing_datasets += 1

    def _ingest_from_compact_or_summary(
        self,
        compact: CompactDatasetSummary | None,
        summary: dict[str, Any] | None,
    ) -> bool:
        """Species / brainRegions: prefer the compact summary already on
        the row (avoids re-touching the per-dataset full summary for
        data we already have). Fall through to the full summary when the
        compact was absent.
        """
        compact_species = compact.species if compact else None
        compact_regions = compact.brainRegions if compact else None
        full_species = summary["species"] if summary else None
        full_regions = summary["brainRegions"] if summary else None

        effective_species = compact_species if compact_species is not None else full_species
        effective_regions = compact_regions if compact_regions is not None else full_regions

        contributed = False
        if effective_species:
            for term in effective_species:
                if _add_ontology_term(term, self._species_seen, self.species):
                    contributed = True
        if effective_regions:
            for term in effective_regions:
                if _add_ontology_term(term, self._brain_regions_seen, self.brain_regions):
                    contributed = True
        return contributed

    def _ingest_from_full_summary(self, summary: dict[str, Any]) -> bool:
        """Strains / sexes / probeTypes: only available on the full summary.
        Skipped entirely when the synthesizer failed for this dataset.
        """
        contributed = False
        for term in (summary.get("strains") or []):
            if _add_ontology_term(term, self._strains_seen, self.strains):
                contributed = True
        for term in (summary.get("sexes") or []):
            if _add_ontology_term(term, self._sexes_seen, self.sexes):
                contributed = True
        for raw_label in (summary.get("probeTypes") or []):
            if _add_probe_type(raw_label, self._probe_types_seen, self.probe_types):
                contributed = True
        return contributed

    def to_response(self) -> FacetsResponse:
        return FacetsResponse(
            species=self.species,
            brainRegions=self.brain_regions,
            strains=self.strains,
            sexes=self.sexes,
            probeTypes=self.probe_types,
            datasetCount=self.contributing_datasets,
            computedAt=_now_iso8601(),
        )


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------

def _add_ontology_term(
    term: Any,
    seen: dict[str, int],
    out: list[OntologyTerm],
) -> bool:
    """Append ``term`` to ``out`` if its ontology-id (or label fallback) has
    not been seen yet. Returns True iff something new was added. Tolerates
    both :class:`OntologyTerm` instances and serialized dicts (facet builder
    has both on hand since full summaries come through as ``model_dump``).
    """
    if isinstance(term, OntologyTerm):
        raw_label: Any = term.label
        raw_ontology_id: Any = term.ontologyId
    elif isinstance(term, dict):
        raw_label = term.get("label")
        raw_ontology_id = term.get("ontologyId")
    else:
        return False
    if not isinstance(raw_label, str) or not raw_label:
        return False
    label: str = raw_label
    ontology_id: str | None = (
        raw_ontology_id if isinstance(raw_ontology_id, str) and raw_ontology_id else None
    )
    key = ontology_id if ontology_id else f"label::{label}"
    if key in seen:
        return False
    seen[key] = len(out)
    out.append(OntologyTerm(label=label, ontologyId=ontology_id))
    return True


def _add_probe_type(
    raw: Any,
    seen: set[str],
    out: list[str],
) -> bool:
    """Append a free-text probe type to ``out`` once, trimmed. Returns True
    iff a new value was added.
    """
    if not isinstance(raw, str):
        return False
    cleaned = raw.strip()
    if not cleaned or cleaned in seen:
        return False
    seen.add(cleaned)
    out.append(cleaned)
    return True


def _row_dataset_id(row: dict[str, Any]) -> str | None:
    for key in ("id", "_id", "datasetId"):
        v = row.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _compact_from_row(row: dict[str, Any]) -> CompactDatasetSummary | None:
    """Parse the embedded compact summary on a catalog row back into a
    :class:`CompactDatasetSummary`. Returns ``None`` when the row doesn't
    carry one (older backend build or summary synth failed).
    """
    raw = row.get("summary")
    if not isinstance(raw, dict):
        return None
    try:
        return CompactDatasetSummary.model_validate(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now_iso8601() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "FACETS_CACHE_KEY",
    "FACETS_CACHE_TTL_SECONDS",
    "FACETS_KEY_PREFIX",
    "FACETS_SCHEMA_VERSION",
    "FacetService",
    "FacetsResponse",
]
