"""Cross-dataset facet aggregator — Plan B B3.

Aggregates distinct structured facts (species / brain regions / strains /
sexes / probe types / licenses) across **all published datasets** into one
:class:`FacetsResponse` the query page uses to populate filter chips.

The primitives are already there:

- :meth:`DatasetService.list_published_with_summaries` walks the catalog in
  pages and embeds a :class:`CompactDatasetSummary` per row (B2's enricher,
  ADR-010-012 pipeline).
- That compact summary carries ``species``, ``brainRegions``, and
  ``citation.license`` directly. For the remaining facets (``strains``,
  ``sexes``, ``probeTypes``) we fetch the full :class:`DatasetSummary` per
  row via :meth:`DatasetSummaryService.build_summary`, which sits behind
  the same 5-minute TTL cache so repeat facet computes inside one TTL
  window pay no extra cloud cost.

Dedup strategy
--------------

- Ontology-typed facets (species/brainRegions/strains/sexes) dedupe first
  by ``OntologyTerm.ontologyId`` and second by a normalized label key
  (``lowercase + collapse-whitespace + strip``). The normalized fallback
  collapses case-identical duplicates (``Caenorhabditis elegans`` reported
  twice from two datasets) and trivial whitespace drift that the strict
  ``label::<exact>`` key used to leak.

  For brain regions specifically, we ALSO key on parenthesized
  abbreviations (``... (BNST)``) extracted from the label — this collapses
  near-duplicates like ``Bed nucleus of the stria terminalis (BNST)`` and
  ``Bed nucleus of stria terminalis (BNST)`` into one entry. Same
  biological entity, different wording — the abbreviation is the
  authoritative shared identifier.

  When two terms collapse, the displayed label is the more-frequently-seen
  cased version (ties broken by first-seen). Per-term seen counts are
  internal; the wire format stays a flat list of distinct
  :class:`OntologyTerm`.
- ``probeTypes`` is a free-text bucket (amendment §3). We dedupe by the
  trimmed label.
- ``licenses`` is a free-text bucket post-:data:`LICENSE_LABELS`
  normalization. The cloud surfaces three concurrent format families
  (``CC-BY-4.0``, the human-readable ``Creative Commons Attribution 4.0
  International``, and camelCase enum-tokens like ``ccByNcSa4_0``). We
  collapse each into its canonical short label so a chip says
  ``CC BY 4.0`` once, not three times.

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
import re
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

# License-label normalization table — maps the three concurrent format
# families the cloud surfaces (camelCase enum tokens, dotted SPDX-ish
# short codes, full human-readable names) onto one canonical short label
# per logical license. Keys are normalized via :func:`_normalize_license_key`
# (lowercase + strip non-alphanumerics) so all three forms collapse to
# the same lookup. Add new entries when the cloud's license enum grows.
#
# Sourced from Creative Commons' canonical naming + the cloud-node enum
# at ``api/src/models/dataset.model.ts`` (license: enum).
_LICENSE_LABEL_ENTRIES: list[tuple[list[str], str]] = [
    (["ccBy4_0", "CC-BY-4.0", "CC BY 4.0", "Creative Commons Attribution 4.0 International"], "CC BY 4.0"),
    (["ccBySa4_0", "CC-BY-SA-4.0", "CC BY-SA 4.0", "Creative Commons Attribution-ShareAlike 4.0 International"], "CC BY-SA 4.0"),
    (["ccByNc4_0", "CC-BY-NC-4.0", "CC BY-NC 4.0", "Creative Commons Attribution-NonCommercial 4.0 International"], "CC BY-NC 4.0"),
    (["ccByNcSa4_0", "CC-BY-NC-SA-4.0", "CC BY-NC-SA 4.0", "Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International"], "CC BY-NC-SA 4.0"),
    (["ccByNd4_0", "CC-BY-ND-4.0", "CC BY-ND 4.0", "Creative Commons Attribution-NoDerivatives 4.0 International"], "CC BY-ND 4.0"),
    (["ccByNcNd4_0", "CC-BY-NC-ND-4.0", "CC BY-NC-ND 4.0", "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International"], "CC BY-NC-ND 4.0"),
    (["ccZero1_0", "CC0-1.0", "CC0 1.0", "Creative Commons Zero 1.0 Universal", "Public Domain"], "CC0 1.0"),
]


def _normalize_license_key(raw: str) -> str:
    """Normalize a license string for table lookup.

    Strips every non-alphanumeric character and lowercases the rest. The
    three on-the-wire formats (``CC-BY-4.0``, ``CC BY 4.0``,
    ``ccBy4_0``, ``Creative Commons Attribution 4.0 International``)
    all collapse to a comparable shape so the lookup table holds three
    distinct strings per logical license without N-way string compares
    at hot-path time.
    """
    return re.sub(r"[^a-z0-9]", "", raw.lower())


LICENSE_LABELS: dict[str, str] = {
    _normalize_license_key(raw): canonical
    for raws, canonical in _LICENSE_LABEL_ENTRIES
    for raw in raws
}


# Brain-region abbreviation extraction. ``Bed nucleus of the stria
# terminalis (BNST)`` and ``Bed nucleus of stria terminalis (BNST)``
# share ``BNST``; we use that as a secondary dedupe key when no
# ontologyId is available. The pattern requires 2-12 alphanumeric chars
# inside the parentheses to avoid false positives on numeric measurements
# or sentence fragments.
_PARENTHESIZED_ABBREV = re.compile(r"\(([A-Za-z0-9]{2,12})\)")


def _normalize_label_key(label: str) -> str:
    """Normalize a label for case/whitespace-insensitive dedupe.

    Lowercases, collapses runs of whitespace into a single space, and
    strips leading/trailing whitespace. Used as the fallback dedupe key
    for ontology terms that lack an ``ontologyId``. Pre-fix repro: cloud
    occasionally surfaces the same species twice (label-only, identical
    casing) when two contributing datasets each reported it
    independently — the strict ``label::<exact>`` key didn't collapse
    them; this normalized one does.
    """
    return re.sub(r"\s+", " ", label.strip().lower())


def _extract_parenthesized_abbrev(label: str) -> str | None:
    """Return the parenthesized abbreviation from ``label``, lowercased,
    if exactly one is present and it's between 2 and 12 alphanumerics.

    Used as a brain-region-specific secondary dedupe key.
    Multiple matches → ``None`` (ambiguous; fall back to label-only).
    """
    matches = _PARENTHESIZED_ABBREV.findall(label)
    if len(matches) != 1:
        return None
    # `re.findall` is typed as `list[Any]` for the no-group / single-group
    # cases, so wrap in `str()` to satisfy mypy's `no-any-return` at the
    # function boundary. The capture group `(\w+)` always yields a string.
    return str(matches[0]).lower()


# ---------------------------------------------------------------------------
# Data-shape contract — mirrored in frontend/src/types/facets.ts
# ---------------------------------------------------------------------------

class FacetsResponse(BaseModel):
    """Distinct-value facets aggregated across all published datasets.

    Empty list for any facet means the aggregation ran and found no values.
    ``datasetCount`` is the number of datasets that contributed at least one
    non-null summary; datasets with ``summary: null`` (synthesizer failed or
    short-circuit miss) are still walked but contribute nothing.

    ``licenses`` is a free-text bucket post-normalization through
    :data:`LICENSE_LABELS`. Multiple raw forms (``CC-BY-4.0``,
    ``ccBy4_0``, ``Creative Commons Attribution 4.0 International``)
    collapse to a single canonical short label per logical license.
    """

    model_config = ConfigDict(extra="forbid")

    species: list[OntologyTerm] = Field(default_factory=list)
    brainRegions: list[OntologyTerm] = Field(default_factory=list)
    strains: list[OntologyTerm] = Field(default_factory=list)
    sexes: list[OntologyTerm] = Field(default_factory=list)
    probeTypes: list[StrictStr] = Field(default_factory=list)
    licenses: list[StrictStr] = Field(default_factory=list)
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
            licenses=len(response.licenses),
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
        self.licenses: list[str] = []

        # ``_*_seen`` maps dedupe-key → index into the corresponding
        # output list. Each entry tracks the per-cased-label seen
        # counter so that, when duplicates collapse, the displayed
        # label is the more-frequently-seen casing (ties broken by
        # first-seen). The label is mutated in-place on the
        # ``OntologyTerm`` after a winning collision.
        self._species_seen: dict[str, _DedupedTermBucket] = {}
        self._brain_regions_seen: dict[str, _DedupedTermBucket] = {}
        self._strains_seen: dict[str, _DedupedTermBucket] = {}
        self._sexes_seen: dict[str, _DedupedTermBucket] = {}
        self._probe_types_seen: set[str] = set()
        # license-key (normalized) → index into self.licenses. The
        # *displayed* canonical label comes from LICENSE_LABELS, so we
        # don't need a per-cased-label counter here.
        self._licenses_seen: dict[str, int] = {}

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
        self._ingest_license(compact, summary)
        if compact is not None or summary is not None:
            self.contributing_datasets += 1

    def _ingest_license(
        self,
        compact: CompactDatasetSummary | None,
        summary: dict[str, Any] | None,
    ) -> None:
        """Aggregate the dataset's license string after canonical
        normalization. Prefers the compact summary's value (already on
        the catalog row); falls back to the full summary's citation.
        """
        raw: str | None = None
        if compact is not None and compact.citation.license:
            raw = compact.citation.license
        elif summary is not None:
            citation = summary.get("citation") or {}
            v = citation.get("license")
            if isinstance(v, str) and v:
                raw = v
        if raw is None:
            return
        canonical = _canonicalize_license(raw)
        if canonical is None:
            return
        key = _normalize_license_key(canonical)
        if key in self._licenses_seen:
            return
        self._licenses_seen[key] = len(self.licenses)
        self.licenses.append(canonical)

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
                if _add_ontology_term(
                    term,
                    self._brain_regions_seen,
                    self.brain_regions,
                    use_paren_abbrev=True,
                ):
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
            licenses=self.licenses,
            datasetCount=self.contributing_datasets,
            computedAt=_now_iso8601(),
        )


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------

class _DedupedTermBucket:
    """Internal bookkeeping for one deduped facet entry.

    Tracks the index into the output list (so the displayed label can
    be mutated in-place when a higher-count casing wins) and a counter
    per cased-label-string. The most-frequently-seen casing is the
    surviving displayed label; ties are broken by first-seen.
    """

    __slots__ = ("counts", "index", "winning_label")

    def __init__(self, *, index: int, label: str) -> None:
        self.index = index
        # cased label → seen count. First-seen entry inserted with count
        # 1; subsequent matches bump.
        self.counts: dict[str, int] = {label: 1}
        self.winning_label = label

    def record(self, label: str) -> str | None:
        """Bump the seen counter for ``label`` and, if the new count
        beats the prior winner, return the new winning label string.
        Returns ``None`` when no swap is needed.
        """
        new_count = self.counts.get(label, 0) + 1
        self.counts[label] = new_count
        # Strict >: ties keep the first-seen winning label.
        if (
            new_count > self.counts.get(self.winning_label, 0)
            and label != self.winning_label
        ):
            self.winning_label = label
            return label
        return None


def _add_ontology_term(
    term: Any,
    seen: dict[str, _DedupedTermBucket],
    out: list[OntologyTerm],
    *,
    use_paren_abbrev: bool = False,
) -> bool:
    """Append ``term`` to ``out`` if its dedupe key (ontologyId, then
    parenthesized abbreviation when ``use_paren_abbrev`` is set, then
    normalized label) has not been seen yet. Returns True iff something
    new was added.

    On a collision: bump the bucket's per-cased-label counter and, if the
    new casing now has the highest count, swap the displayed label of the
    already-emitted :class:`OntologyTerm` in-place. Pre-fix: collisions
    were silently ignored; the first-seen casing was the only one ever
    surfaced.

    Tolerates both :class:`OntologyTerm` instances and serialized dicts
    (facet builder has both on hand since full summaries come through as
    ``model_dump``).
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

    # Dedupe key resolution, in priority order:
    # 1. ontologyId (most authoritative — same provider id wins).
    # 2. Parenthesized abbreviation (brain-region only) — collapses
    #    "Bed nucleus of the stria terminalis (BNST)" with
    #    "Bed nucleus of stria terminalis (BNST)".
    # 3. Normalized label (lowercase + collapse-whitespace + strip) —
    #    collapses case-identical and trivial-whitespace duplicates.
    key: str
    if ontology_id:
        key = f"oid::{ontology_id}"
    else:
        abbrev = (
            _extract_parenthesized_abbrev(label) if use_paren_abbrev else None
        )
        key = (
            f"abbrev::{abbrev}" if abbrev else f"norm::{_normalize_label_key(label)}"
        )

    bucket = seen.get(key)
    if bucket is not None:
        new_winner = bucket.record(label)
        if new_winner is not None:
            # In-place label swap on the already-emitted term so the
            # output list reflects the most-frequently-seen casing
            # without us having to re-emit or sort.
            existing = out[bucket.index]
            out[bucket.index] = OntologyTerm(
                label=new_winner, ontologyId=existing.ontologyId,
            )
        return False
    seen[key] = _DedupedTermBucket(index=len(out), label=label)
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


def _canonicalize_license(raw: str) -> str | None:
    """Return the canonical short label for a raw license string, or
    ``None`` when the input is empty / not stringly meaningful.

    Unknown licenses pass through with whitespace trimmed (so a
    novel cloud-side enum value still surfaces as a chip rather than
    being silently dropped). Known licenses go through
    :data:`LICENSE_LABELS` for normalization.
    """
    cleaned = raw.strip()
    if not cleaned:
        return None
    canonical = LICENSE_LABELS.get(_normalize_license_key(cleaned))
    return canonical if canonical is not None else cleaned


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
    "LICENSE_LABELS",
    "FacetService",
    "FacetsResponse",
]
