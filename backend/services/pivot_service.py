"""PivotService — Plan B B6e grain-selectable pivot v1.

Behind feature flag ``FEATURE_PIVOT_V1``. Produces a pivot table keyed by one
of three "obvious" grains: ``subject``, ``session``, ``element``. Rows are
one-per-grain-entity with grain-specific columns. Subject grain ships first;
session and element follow the same row shape discipline per amendment §4.B6e.

Row shape
---------

The response envelope is flexible-by-grain:

    {
      "grain": "subject",
      "columns": [{"key": "...", "label": "..."}, ...],
      "rows":    [{"columnKey": value, ...}, ...],
    }

Each grain's column set is documented on its projection helper below.
Multi-valued cells (e.g. a subject with two strain companions) are joined
into comma-separated strings — matching NDI-matlab's default
``ndi.fun.table.join`` aggregation (amendment Report C §4.B6a).

Cloud calls
-----------

Query shape per grain:

- ``subject``:  ``ndiquery isa=subject``  + bulk-fetch. Enrichments:
  openminds_subject (species/strain/sex), treatment (optional).
- ``session``:  ``ndiquery isa=session``  + bulk-fetch. Enrichments:
  subject (count per session), openminds_subject (species rollup).
- ``element``:  ``ndiquery isa=element``  (probe fallback) + bulk-fetch.
  Enrichments: subject, probe_location, openminds_subject.

All bulk-fetch batches go through ``Semaphore(3)`` — matches the other
services' concurrency ceiling and leaves headroom under Lambda's 29s cap.

Cache key
---------

``pivot:v1:{dataset_id}:{grain}:{user_scope_for(session)}``. TTL 5 minutes
per the B1/B6e freshness-over-TTL-economy posture (amendment §4.B3).

HTTP boundary
-------------

Every cloud call routes through :mod:`backend.clients.ndi_cloud`. Reuses
``_openminds_name_and_ontology``, ``_attach_openminds_enrichment``,
``_probe_location_split``, ``_probe_locations_for``, ``_element_subject_ndi``
and friends from :mod:`backend.services.summary_table_service` — Schema-A/B
dispatch stays in one place.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..auth.session import SessionData, user_scope_for
from ..cache.redis_table import RedisTableCache
from ..clients.ndi_cloud import BULK_FETCH_MAX, NdiCloudClient
from ..errors import NotFound, ValidationFailed
from ..observability.logging import get_logger
from .summary_table_service import (
    _attach_openminds_enrichment,
    _clean,
    _element_subject_ndi,
    _extract_ids,
    _first,
    _index_by_ndi_id,
    _ndi_id,
    _openminds_name_and_ontology,
    _probe_location_split,
    _probe_locations_for,
    _project_name,
)

log = get_logger(__name__)

PIVOT_SCHEMA_VERSION = "pivot:v1"
PIVOT_KEY_PREFIX = "pivot:v1"
PIVOT_CACHE_TTL_SECONDS = 5 * 60  # freshness > TTL economy (amendment §4.B3)
MAX_CONCURRENT_BULK_FETCH = 3  # matches summary_table_service baseline

Grain = Literal["subject", "session", "element"]
SUPPORTED_GRAINS: tuple[Grain, ...] = ("subject", "session", "element")


# ---------------------------------------------------------------------------
# Response model — flexible row shape with typed header.
# ---------------------------------------------------------------------------

class PivotColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: StrictStr
    label: StrictStr


class PivotResponse(BaseModel):
    """Grain-selected pivot envelope.

    ``rows`` is ``list[dict[str, Any]]`` keyed on column ``key``. Per-grain
    row shape is documented on the projection helper that produced it
    (``_row_subject_pivot`` / ``_row_session_pivot`` / ``_row_element_pivot``).
    """

    model_config = ConfigDict(extra="forbid")

    datasetId: StrictStr
    grain: StrictStr
    columns: list[PivotColumn]
    rows: list[dict[str, Any]]
    computedAt: StrictStr
    schemaVersion: Literal["pivot:v1"] = "pivot:v1"
    totalRows: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PivotService:
    """Compose a grain-keyed pivot by chaining ndiquery + bulk-fetch +
    minimal enrichment. Stateless per request; one instance per
    ``NdiCloudClient`` (wired in :mod:`backend.routers._deps`).
    """

    def __init__(
        self,
        cloud: NdiCloudClient,
        *,
        cache: RedisTableCache | None = None,
    ) -> None:
        self.cloud = cloud
        self.cache = cache

    async def pivot_by_grain(
        self,
        dataset_id: str,
        grain: str,
        *,
        session: SessionData | None,
    ) -> dict[str, Any]:
        """Return the pivot for a grain. Raises:

        - ``ValidationFailed`` when ``grain`` is not in ``SUPPORTED_GRAINS``.
        - ``NotFound`` when the grain has zero docs on this dataset (per
          ``document-class-counts``). The 404 is pre-computed — we don't
          spend a ndiquery on empty grains.
        """
        if grain not in SUPPORTED_GRAINS:
            raise ValidationFailed(
                f"Unsupported pivot grain: {grain!r}. "
                f"Supported grains: {', '.join(SUPPORTED_GRAINS)}.",
                details={"grain": grain, "supported": list(SUPPORTED_GRAINS)},
            )

        access_token = session.access_token if session else None

        # 404 gate: consult class counts so we don't even hit ndiquery for
        # grains that have zero docs on this dataset.
        counts_raw = await self.cloud.get_document_class_counts(
            dataset_id, access_token=access_token,
        )
        if not _grain_present_in_counts(grain, counts_raw):
            raise NotFound(
                f"No {grain!r} documents in this dataset.",
                log_context={"dataset_id": dataset_id, "grain": grain},
            )

        if self.cache is not None:
            key = pivot_cache_key(dataset_id, grain, session)
            return await self.cache.get_or_compute(
                key,
                lambda: self._build(
                    dataset_id, grain, access_token=access_token,
                ),
            )
        return await self._build(dataset_id, grain, access_token=access_token)

    async def _build(
        self,
        dataset_id: str,
        grain: str,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        sem = asyncio.Semaphore(MAX_CONCURRENT_BULK_FETCH)

        if grain == "subject":
            columns, rows = await self._build_subject(
                dataset_id, access_token=access_token, sem=sem,
            )
        elif grain == "session":
            columns, rows = await self._build_session(
                dataset_id, access_token=access_token, sem=sem,
            )
        elif grain == "element":
            columns, rows = await self._build_element(
                dataset_id, access_token=access_token, sem=sem,
            )
        else:  # pragma: no cover — guarded earlier in pivot_by_grain
            raise ValidationFailed(f"Unsupported pivot grain: {grain!r}")

        envelope = PivotResponse(
            datasetId=dataset_id,
            grain=grain,
            columns=[PivotColumn(key=c["key"], label=c["label"]) for c in columns],
            rows=rows,
            computedAt=_now_iso8601(),
            totalRows=len(rows),
        ).model_dump(mode="json")
        log.info(
            "pivot.build",
            dataset_id=dataset_id,
            grain=grain,
            rows=len(rows),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return envelope

    # --- Per-grain builders ------------------------------------------------

    async def _build_subject(
        self,
        dataset_id: str,
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        subjects_task = self._fetch_class(
            dataset_id, "subject", access_token=access_token, sem=sem,
        )
        openminds_task = self._fetch_class(
            dataset_id, "openminds_subject",
            access_token=access_token, sem=sem,
        )
        subjects, openminds_docs = await asyncio.gather(
            subjects_task, openminds_task,
        )
        # Attach enrichment so _openminds_name_and_ontology can dispatch
        # Schema-A/B in one pass per subject doc.
        _attach_openminds_enrichment(subjects, openminds_docs)
        rows = [_row_subject_pivot(d) for d in subjects]
        return SUBJECT_PIVOT_COLUMNS, rows

    async def _build_session(
        self,
        dataset_id: str,
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        # Sessions are a logical grouping of subjects; the cloud stores each
        # subject with a ``base.session_id`` pointer. Rather than require a
        # dedicated `session` document class (not always present on older
        # datasets), we roll the pivot up from subjects grouped by
        # session_id. This matches the MATLAB/NDI-python convention where
        # a session is "the experimental container" for subjects.
        subjects_task = self._fetch_class(
            dataset_id, "subject", access_token=access_token, sem=sem,
        )
        openminds_task = self._fetch_class(
            dataset_id, "openminds_subject",
            access_token=access_token, sem=sem,
        )
        subjects, openminds_docs = await asyncio.gather(
            subjects_task, openminds_task,
        )
        _attach_openminds_enrichment(subjects, openminds_docs)
        rows = _session_pivot_rows(subjects)
        return SESSION_PIVOT_COLUMNS, rows

    async def _build_element(
        self,
        dataset_id: str,
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        elements_task = self._fetch_class_any(
            dataset_id, ["element", "probe"],
            access_token=access_token, sem=sem,
        )
        subjects_task = self._fetch_class(
            dataset_id, "subject", access_token=access_token, sem=sem,
        )
        probe_locations_task = self._fetch_class(
            dataset_id, "probe_location",
            access_token=access_token, sem=sem,
        )
        openminds_task = self._fetch_class(
            dataset_id, "openminds_subject",
            access_token=access_token, sem=sem,
        )
        elements, subjects, probe_locations, openminds_docs = await asyncio.gather(
            elements_task, subjects_task, probe_locations_task, openminds_task,
        )
        _attach_openminds_enrichment(subjects, openminds_docs)
        subject_by_ndi = _index_by_ndi_id(subjects)
        rows = [
            _row_element_pivot(el, subject_by_ndi, probe_locations)
            for el in elements
        ]
        return ELEMENT_PIVOT_COLUMNS, rows

    # --- Cloud plumbing ---------------------------------------------------

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

    async def _fetch_class_any(
        self,
        dataset_id: str,
        candidates: list[str],
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        for c in candidates:
            docs = await self._fetch_class(
                dataset_id, c, access_token=access_token, sem=sem,
            )
            if docs:
                return docs
        return []

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


# ---------------------------------------------------------------------------
# Cache key helper (public for tests)
# ---------------------------------------------------------------------------

def pivot_cache_key(
    dataset_id: str, grain: str, session: SessionData | None,
) -> str:
    return f"{PIVOT_KEY_PREFIX}:{dataset_id}:{grain}:{user_scope_for(session)}"


# ---------------------------------------------------------------------------
# Grain-presence gate against /document-class-counts
# ---------------------------------------------------------------------------

def _grain_present_in_counts(grain: str, counts_raw: dict[str, Any]) -> bool:
    """A grain is "present" when any of its candidate class names has ≥1.

    Sessions may be reported as ``session`` or ``session_in_a_dataset``.
    Elements may be reported as ``element`` or ``probe`` on older datasets.
    """
    cc = counts_raw.get("classCounts") or {}
    candidates = {
        "subject": ["subject"],
        "session": ["session", "session_in_a_dataset"],
        "element": ["element", "probe"],
    }.get(grain, [])
    return any(int(cc.get(name) or 0) > 0 for name in candidates)


# ---------------------------------------------------------------------------
# Column definitions — per-grain, camelCase keys matching summary_table_service
# ---------------------------------------------------------------------------

# Subject-grain pivot: one row per subject. Tracks NDI-matlab's canonical
# 13-column subject shape minus dynamic treatment columns (those remain the
# summary-table view's job — they're unbounded in count). Keys align with
# ``frontend/src/data/table-column-definitions.ts::subject_*`` so tooltips
# already render without duplication.
SUBJECT_PIVOT_COLUMNS: list[dict[str, str]] = [
    {"key": "subjectDocumentIdentifier", "label": "Subject Doc ID"},
    {"key": "subjectLocalIdentifier",    "label": "Local Identifier"},
    {"key": "sessionDocumentIdentifier", "label": "Session Doc ID"},
    {"key": "speciesName",               "label": "Species"},
    {"key": "speciesOntology",           "label": "Species Ontology"},
    {"key": "strainName",                "label": "Strain"},
    {"key": "strainOntology",            "label": "Strain Ontology"},
    {"key": "biologicalSexName",         "label": "Sex"},
    {"key": "biologicalSexOntology",     "label": "Sex Ontology"},
]

# Session-grain pivot: one row per distinct ``base.session_id`` among the
# subject docs. Columns aggregate across subjects that share a session.
SESSION_PIVOT_COLUMNS: list[dict[str, str]] = [
    {"key": "sessionDocumentIdentifier", "label": "Session Doc ID"},
    {"key": "subjectCount",              "label": "Subjects"},
    {"key": "speciesName",               "label": "Species (aggregated)"},
    {"key": "strainName",                "label": "Strains (aggregated)"},
    {"key": "biologicalSexName",         "label": "Sexes (aggregated)"},
    {"key": "subjectDocumentIdentifiers","label": "Subject Doc IDs"},
]

# Element-grain pivot: one row per element doc. Reuses the probe/element
# 9-col default but keeps subject cross-reference separate so the pivot view
# can link back.
ELEMENT_PIVOT_COLUMNS: list[dict[str, str]] = [
    {"key": "probeDocumentIdentifier",   "label": "Probe Doc ID"},
    {"key": "probeName",                 "label": "Name"},
    {"key": "probeType",                 "label": "Type"},
    {"key": "probeReference",            "label": "Reference"},
    {"key": "probeLocationName",         "label": "Probe Location"},
    {"key": "probeLocationOntology",     "label": "Probe Location Ontology"},
    {"key": "cellTypeName",              "label": "Cell Type"},
    {"key": "cellTypeOntology",          "label": "Cell Type Ontology"},
    {"key": "subjectDocumentIdentifier", "label": "Subject Doc ID"},
]


# ---------------------------------------------------------------------------
# Row projections
# ---------------------------------------------------------------------------

def _row_subject_pivot(subject_doc: dict[str, Any]) -> dict[str, Any]:
    """9-column subject pivot row.

    Uses ``_openminds_name_and_ontology`` for Schema-A/B dispatch — Species,
    BiologicalSex (Schema A → ``preferredOntologyIdentifier``) and Strain
    (Schema B → ``ontologyIdentifier``) surface under the same call.
    """
    species_name, species_ontology = _openminds_name_and_ontology(subject_doc, "Species")
    strain_name, strain_ontology = _openminds_name_and_ontology(subject_doc, "Strain")
    sex_name, sex_ontology = _openminds_name_and_ontology(subject_doc, "BiologicalSex")

    base = (subject_doc.get("data") or {}).get("base") or {}
    subj = (subject_doc.get("data") or {}).get("subject") or {}

    local_id = _clean(subj.get("local_identifier")) or _clean(base.get("name"))

    return {
        "subjectDocumentIdentifier": _clean(base.get("id")) or _ndi_id(subject_doc),
        "subjectLocalIdentifier":    local_id,
        "sessionDocumentIdentifier": _clean(base.get("session_id")),
        "speciesName":               species_name,
        "speciesOntology":           species_ontology,
        "strainName":                strain_name,
        "strainOntology":            strain_ontology,
        "biologicalSexName":         sex_name,
        "biologicalSexOntology":     sex_ontology,
    }


def _session_pivot_rows(
    subject_docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group subjects by ``base.session_id`` and aggregate.

    Multi-valued cells are joined with ", " — matches NDI-matlab's
    ``ndi.fun.table.join`` default (amendment Report C §2.2). Rows with no
    session_id are bucketed under the literal empty-string key so they
    still surface rather than silently dropped.
    """
    by_session: dict[str, list[dict[str, Any]]] = {}
    for d in subject_docs:
        base = (d.get("data") or {}).get("base") or {}
        sid = _clean(base.get("session_id")) or ""
        key = sid if isinstance(sid, str) else ""
        by_session.setdefault(key, []).append(d)

    rows: list[dict[str, Any]] = []
    for session_id, subjects in by_session.items():
        species = _collect_distinct(
            subjects, lambda d: _openminds_name_and_ontology(d, "Species")[0],
        )
        strains = _collect_distinct(
            subjects, lambda d: _openminds_name_and_ontology(d, "Strain")[0],
        )
        sexes = _collect_distinct(
            subjects, lambda d: _openminds_name_and_ontology(d, "BiologicalSex")[0],
        )
        subject_doc_ids = _collect_distinct(
            subjects,
            lambda d: _clean(((d.get("data") or {}).get("base") or {}).get("id"))
                      or _ndi_id(d),
        )
        rows.append({
            "sessionDocumentIdentifier": session_id or None,
            "subjectCount":              len(subjects),
            "speciesName":               ", ".join(species) if species else None,
            "strainName":                ", ".join(strains) if strains else None,
            "biologicalSexName":         ", ".join(sexes) if sexes else None,
            "subjectDocumentIdentifiers":
                ", ".join(subject_doc_ids) if subject_doc_ids else None,
        })
    # Stable order: largest-subject-count first, then session id.
    rows.sort(
        key=lambda r: (
            -int(r.get("subjectCount") or 0),
            str(r.get("sessionDocumentIdentifier") or ""),
        ),
    )
    return rows


def _row_element_pivot(
    element_doc: dict[str, Any],
    subject_by_ndi: dict[str, dict[str, Any]],
    probe_locations: list[dict[str, Any]],
) -> dict[str, Any]:
    """9-column element pivot row (probe + location + cell-type join).

    Subject cross-reference surfaces as ``subjectDocumentIdentifier`` so the
    frontend can link back to the subject pivot row.
    """
    locations = _probe_locations_for(element_doc, probe_locations)
    (loc_name, loc_ont), (cell_name, cell_ont) = _probe_location_split(locations)
    subject_ndi = _element_subject_ndi(element_doc)
    return {
        "probeDocumentIdentifier":   _ndi_id(element_doc),
        "probeName":                 _clean(_first(
                                          element_doc, "element.name", "probe.name",
                                     ))
                                     or _project_name(element_doc)
                                     or _clean(_first(element_doc, "base.name")),
        "probeType":                 _clean(_first(
                                          element_doc, "element.type", "probe.type",
                                          "element.ndi_element_class",
                                          "probe.class", "type",
                                     )),
        "probeReference":            _clean(_first(
                                          element_doc, "element.reference",
                                          "probe.reference", "reference",
                                     )),
        "probeLocationName":         loc_name,
        "probeLocationOntology":     loc_ont,
        "cellTypeName":              cell_name,
        "cellTypeOntology":          cell_ont,
        "subjectDocumentIdentifier": subject_ndi,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_distinct(
    docs: list[dict[str, Any]],
    pick: Any,
) -> list[str]:
    """First-seen-wins list of distinct non-empty string values."""
    out: list[str] = []
    seen: set[str] = set()
    for d in docs:
        v = pick(d)
        if not isinstance(v, str):
            continue
        v = v.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _now_iso8601() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# Touched imports we use in helper dispatch — keep them reachable for tests.
__all__ = [
    "ELEMENT_PIVOT_COLUMNS",
    "MAX_CONCURRENT_BULK_FETCH",
    "PIVOT_CACHE_TTL_SECONDS",
    "PIVOT_KEY_PREFIX",
    "PIVOT_SCHEMA_VERSION",
    "SESSION_PIVOT_COLUMNS",
    "SUBJECT_PIVOT_COLUMNS",
    "SUPPORTED_GRAINS",
    "Grain",
    "PivotColumn",
    "PivotResponse",
    "PivotService",
    "pivot_cache_key",
]
