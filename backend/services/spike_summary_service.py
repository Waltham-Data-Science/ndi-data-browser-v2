"""spike_summary_service — pull per-unit spike trains from
``vmspikesummary`` documents and shape them for spike-raster and/or
ISI histogram rendering.

This is the Python port of the chat-side TS handler at
``ndi-cloud-app/apps/web/lib/ndi/tools/fetch-spike-summary.ts``.
Moving the orchestration to Railway keeps the heart of NDI processing
next to ndi-python where it belongs; the TS handler shrinks to a thin
proxy after this lands.

Discovery — three modes, cheapest first:

  1. ``unit_doc_id`` — direct fetch of a single vmspikesummary doc.
     Cheapest path; used when the caller has already resolved which
     unit it wants (chained from a query).
  2. ``unit_name_match`` — substring filter against the doc's
     ``vmspikesummary.name`` field. Hits ``/ndiquery`` with a
     two-clause structured query.
  3. Bare dataset scan — first N vmspikesummary docs in the dataset.
     Use for "show me a raster from dataset X".

Spike-times path
────────────────
The TS implementation extracts ``spike_times`` directly from the
document's JSON body (``data.vmspikesummary.spike_times`` with
fallbacks to ``spiketimes`` and ``sample_times``). vmspikesummary
docs inline their spike data in the JSON; there is no separate
binary file to open. We preserve that canonical path here.

Caller-facing differences vs the TS implementation
──────────────────────────────────────────────────
The router returns RAW per-unit data
(``{units: [{name, doc_id, spike_times, isi_intervals}], ...}``)
NOT the chat-specific ``chart_payloads`` wrapper. The TS layer
reshapes raw data into chart_payloads on the chat side; the
workspace consumes raw data directly. This keeps the backend
agnostic to UI framing.

Soft-error envelope
───────────────────
When a document is found but its spike-times array is missing or
unparseable, we surface a per-unit ``{error, error_kind:
'decode_failed'}`` rather than crashing the whole request. The
``error_kind`` taxonomy mirrors the existing /signal route so the
chat tool / workspace can branch on it.
"""
from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..auth.session import SessionData
from ..clients.ndi_cloud import NdiCloudClient
from ..observability.logging import get_logger
from .document_service import DocumentService

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables — module-level so tests can monkeypatch and so the constants
# are reachable from tests without re-importing internals.
# ---------------------------------------------------------------------------

# Server-side cap on per-call unit count. Mirrors the TS handler's
# MAX_UNITS_HARD. The chart components also cap (SpikeRaster at 50) but
# the right place to enforce is here so we never download more than we'll
# render.
MAX_UNITS_HARD = 50
DEFAULT_MAX_UNITS = 10

# Per-unit spike-times cap. Mirrors the TS handler's stride-sample limit
# of 500. Plotly comfortably renders this density and the visual shape is
# preserved for any reasonable spike train. The full spike list is used
# for ISI computation BEFORE this cap is applied so the histogram
# remains statistically accurate.
MAX_SPIKES_PER_UNIT = 5000

# Per-unit ISI-intervals cap. The TS handler caps the consolidated
# payload at 5000 (across all units) but our raw-data shape returns
# per-unit arrays, so the cap is applied per-unit.
MAX_ISI_INTERVALS_PER_UNIT = 5000


# ---------------------------------------------------------------------------
# Pydantic request/response models.
#
# Field aliases let the router accept either camelCase (TS proxy passing
# through its existing input) or snake_case body keys without the caller
# having to translate.
# ---------------------------------------------------------------------------


SpikeKind = Literal["raster", "isi_histogram", "both"]


class SpikeSummaryRequest(BaseModel):
    """Input shape mirrors the TS ``fetchSpikeSummaryInput`` schema
    so the TS handler can pass its input through verbatim.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    dataset_id: str = Field(..., alias="datasetId", min_length=1)
    unit_doc_id: str | None = Field(default=None, alias="unitDocId", min_length=1)
    unit_name_match: str | None = Field(
        default=None, alias="unitNameMatch", min_length=1,
    )
    kind: SpikeKind = "both"
    t_window: tuple[float, float] | None = Field(default=None, alias="tWindow")
    max_units: int | None = Field(
        default=None, alias="maxUnits", ge=1, le=MAX_UNITS_HARD,
    )
    title: str | None = Field(default=None, max_length=160)


class SpikeSummaryUnit(BaseModel):
    """One unit's contribution to the response.

    ``spike_times`` is included when ``kind`` is ``raster`` or ``both``.
    ``isi_intervals`` is included when ``kind`` is ``isi_histogram``
    or ``both``. Both are absent when the unit's binary decode failed
    (``error`` populated instead).
    """

    name: str
    doc_id: str
    spike_times: list[float] | None = None
    isi_intervals: list[float] | None = None
    # When set, the unit's spike-times array was unparseable. The unit
    # is still included in `units` so callers see a placeholder + the
    # decode reason; soft-error envelope matches the /signal route.
    error: str | None = None
    error_kind: str | None = None


class SpikeSummaryResponse(BaseModel):
    """Top-level response. ``total_matching`` is the count BEFORE the
    ``max_units`` slice — callers can disclose "showed 10 of N" when
    truncated.
    """

    units: list[SpikeSummaryUnit]
    total_matching: int
    kind: SpikeKind
    # Diagnostic — populated when no units matched / decoded so the
    # caller can explain or retry. Empty-string ``error`` is reserved
    # for "no failure"; consumers should check ``len(units)``.
    error: str | None = None
    error_kind: str | None = None


# ---------------------------------------------------------------------------
# Public orchestration entry point
# ---------------------------------------------------------------------------


async def compute_spike_summary(
    request: SpikeSummaryRequest,
    *,
    document_service: DocumentService,
    cloud: NdiCloudClient,
    session: SessionData | None,
) -> SpikeSummaryResponse:
    """Orchestrate vmspikesummary discovery + per-unit spike-train
    extraction.

    Parameters
    ----------
    request:
        Validated input (see :class:`SpikeSummaryRequest`).
    document_service:
        Used for the ``unit_doc_id`` single-doc fetch path. The detail
        endpoint handles ndiId-vs-Mongo-id resolution.
    cloud:
        Used directly for the ``unit_name_match`` + bare-scan
        ndiquery calls. We bypass ``QueryService`` here because its
        scope-validator enforces a Mongo-ObjectId regex that's
        redundant with the path validator and would reject the
        free-form dataset IDs the rest of the stack accepts.
    session:
        Optional session — propagated as ``access_token`` so private
        datasets work for logged-in users while public datasets work
        anonymously.
    """
    access_token = session.access_token if session else None
    max_units = min(request.max_units or DEFAULT_MAX_UNITS, MAX_UNITS_HARD)

    docs, total_matching = await _resolve_units(
        request,
        document_service=document_service,
        cloud=cloud,
        access_token=access_token,
        max_units=max_units,
    )

    if not docs:
        return SpikeSummaryResponse(
            units=[],
            total_matching=0,
            kind=request.kind,
            error=_empty_reason(request),
            error_kind="no_matches",
        )

    units: list[SpikeSummaryUnit] = []
    for doc in docs:
        doc_id = _pick_doc_id(doc)
        name = _pick_unit_name(doc, doc_id)
        raw_spikes = _extract_spike_times(doc)
        if raw_spikes is None or len(raw_spikes) == 0:
            # Soft error per doc — same envelope as /signal so the
            # chat tool can branch on `error_kind`. The doc is kept
            # in the response so the caller sees which unit failed.
            units.append(
                SpikeSummaryUnit(
                    name=name,
                    doc_id=doc_id,
                    error=(
                        "vmspikesummary doc had no parseable spike_times "
                        "array (checked data.vmspikesummary.spike_times, "
                        "spiketimes, sample_times)"
                    ),
                    error_kind="decode_failed",
                ),
            )
            continue

        # t_window filter — done BEFORE the spike-count cap so the cap
        # bounds the rendered density, not the unfiltered density.
        spikes = _apply_t_window(raw_spikes, request.t_window)
        if len(spikes) == 0:
            # Window emptied the unit. Skip silently — the unit isn't
            # "failed" per se, just outside the requested window.
            continue

        spike_times = _build_spike_field(spikes, request.kind)
        isi_intervals = _build_isi_field(spikes, request.kind)
        units.append(
            SpikeSummaryUnit(
                name=name,
                doc_id=doc_id,
                spike_times=spike_times,
                isi_intervals=isi_intervals,
            ),
        )

    # Stable name-order so the response is deterministic for callers
    # iterating in display order. ``unit_doc_id`` (single-doc path)
    # produces a one-element list so this is a no-op there.
    units.sort(key=lambda u: u.name.lower())

    return SpikeSummaryResponse(
        units=units,
        total_matching=total_matching,
        kind=request.kind,
    )


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


async def _resolve_units(
    request: SpikeSummaryRequest,
    *,
    document_service: DocumentService,
    cloud: NdiCloudClient,
    access_token: str | None,
    max_units: int,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(docs, total_matching)``.

    Three modes (mirrors the TS handler):
      1. ``unit_doc_id`` — single-doc fetch (one doc, total=1).
      2. ``unit_name_match`` — ndiquery with ``isa(vmspikesummary)``
         + ``contains_string(vmspikesummary.name, <substr>)``.
      3. Bare scan — ndiquery with just ``isa(vmspikesummary)``.
    """
    if request.unit_doc_id:
        try:
            doc = await document_service.detail(
                request.dataset_id,
                request.unit_doc_id,
                access_token=access_token,
            )
        except Exception as exc:
            log.warning(
                "spike_summary.single_doc_fetch_failed",
                dataset_id=request.dataset_id,
                doc_id=request.unit_doc_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ([], 0)
        return ([doc], 1)

    searchstructure: list[dict[str, Any]] = [
        {"operation": "isa", "param1": "vmspikesummary"},
    ]
    if request.unit_name_match:
        searchstructure.append({
            "operation": "contains_string",
            "field": "vmspikesummary.name",
            "param1": request.unit_name_match,
        })
    try:
        body = await cloud.ndiquery(
            searchstructure=searchstructure,
            scope=request.dataset_id,
            access_token=access_token,
        )
    except Exception as exc:
        log.warning(
            "spike_summary.query_failed",
            dataset_id=request.dataset_id,
            unit_name_match=request.unit_name_match,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return ([], 0)
    docs = list(body.get("documents") or [])
    total = len(docs)
    return (docs[:max_units], total)


def _empty_reason(request: SpikeSummaryRequest) -> str:
    if request.unit_doc_id:
        return (
            f"No vmspikesummary document {request.unit_doc_id} "
            f"in dataset {request.dataset_id}"
        )
    if request.unit_name_match:
        return (
            f"No vmspikesummary documents matched "
            f"name~\"{request.unit_name_match}\" in dataset "
            f"{request.dataset_id}"
        )
    return f"No vmspikesummary documents in dataset {request.dataset_id}"


# ---------------------------------------------------------------------------
# Field extraction — field-path probe order mirrors the TS handler so
# behavior stays consistent across the two implementations.
# ---------------------------------------------------------------------------


def _extract_spike_times(doc: dict[str, Any]) -> list[float] | None:
    """Extract the spike-times array from a vmspikesummary doc body.

    Probe order (most-likely → least-likely):
      1. ``data.vmspikesummary.spike_times``
      2. ``data.vmspikesummary.spiketimes``
      3. ``data.vmspikesummary.sample_times`` ← the schema-canonical name

    Returns None when no array of numbers is found at any candidate
    path. Caller handles the empty case by surfacing a per-unit soft
    error.

    Non-numeric entries are skipped silently (matches the TS handler);
    a doc with mixed-type entries returns the numeric subset.
    """
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        return None
    inner = data.get("vmspikesummary")
    if not isinstance(inner, dict):
        return None
    for key in ("spike_times", "spiketimes", "sample_times"):
        v = inner.get(key)
        if not isinstance(v, list) or not v:
            continue
        nums: list[float] = []
        for x in v:
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                # Guard against NaN/inf which would poison downstream
                # math; matches the TS handler's Number.isFinite check.
                fx = float(x)
                if _is_finite(fx):
                    nums.append(fx)
            elif isinstance(x, str):
                try:
                    parsed = float(x)
                except (TypeError, ValueError):
                    continue
                if _is_finite(parsed):
                    nums.append(parsed)
        if nums:
            return nums
    return None


def _is_finite(v: float) -> bool:
    return math.isfinite(v)


def _pick_doc_id(doc: dict[str, Any]) -> str:
    for key in ("id", "_id", "ndiId"):
        v = doc.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _pick_unit_name(doc: dict[str, Any], doc_id: str) -> str:
    """Prefer ``data.vmspikesummary.name``, then top-level ``name``,
    then a synthesized name from the doc ID tail.
    """
    data = doc.get("data")
    if isinstance(data, dict):
        inner = data.get("vmspikesummary")
        if isinstance(inner, dict):
            n = inner.get("name")
            if isinstance(n, str) and n:
                return n[:80]
    top = doc.get("name")
    if isinstance(top, str) and top:
        return top[:80]
    return f"Unit {doc_id[-6:]}" if doc_id else "Unit"


# ---------------------------------------------------------------------------
# Per-unit computation — t_window filter, stride-sample, ISI compute.
# Pure functions kept module-level so they're trivially unit-testable.
# ---------------------------------------------------------------------------


def _apply_t_window(
    spikes: list[float], window: tuple[float, float] | None,
) -> list[float]:
    if window is None:
        return spikes
    t0, t1 = window
    return [t for t in spikes if t0 <= t <= t1]


def _build_spike_field(
    spikes: list[float], kind: SpikeKind,
) -> list[float] | None:
    """Cap + return the spike-times list when ``kind`` requests it,
    None otherwise. ``kind == 'isi_histogram'`` omits the field so the
    response stays compact for histogram-only callers.
    """
    if kind == "isi_histogram":
        return None
    return _stride_sample(spikes, MAX_SPIKES_PER_UNIT)


def _build_isi_field(
    spikes: list[float], kind: SpikeKind,
) -> list[float] | None:
    """Compute ISI intervals in MILLISECONDS from the FULL spike-times
    list (not the capped one) so the histogram's statistical
    accuracy is preserved. Then stride-sample the intervals before
    returning to bound wire size.

    Returns None when ``kind == 'raster'`` so raster-only callers get
    a compact response.
    """
    if kind == "raster":
        return None
    if len(spikes) < 2:
        return []
    sorted_spikes = np.sort(np.asarray(spikes, dtype=np.float64))
    diffs_ms = np.diff(sorted_spikes) * 1000.0
    # Drop non-finite / non-positive intervals — matches the TS
    # handler's defensive filter. Spike times sorted ascending means
    # diff is always >= 0 but a duplicate timestamp produces 0 which
    # is meaningless for an ISI histogram.
    intervals = [float(d) for d in diffs_ms.tolist() if _is_finite(d) and d > 0]
    return _stride_sample(intervals, MAX_ISI_INTERVALS_PER_UNIT)


def _stride_sample(values: list[float], cap: int) -> list[float]:
    """Stride-sample down to ``cap`` entries preserving first + last.

    Mirrors :func:`backend.services.tabular_query_service._stride_sample`
    (and the TS handler's ``strideSample``). When ``len(values) <=
    cap`` returns a copy.
    """
    n = len(values)
    if n <= cap:
        return list(values)
    if cap <= 2:
        return [values[0], values[-1]][:cap]
    step = (n - 1) / (cap - 1)
    seen: set[int] = set()
    out: list[float] = []
    for i in range(cap):
        idx = round(i * step)
        if idx in seen:
            continue
        seen.add(idx)
        out.append(values[idx])
    return out
