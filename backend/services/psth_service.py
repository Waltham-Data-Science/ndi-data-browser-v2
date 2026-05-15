"""psth_service — peri-stimulus time histogram orchestration.

PSTH is the canonical sensory-neuroscience visualization: align a unit's
spike train to a series of stimulus events, count spikes per fixed-width
bin in a [t0, t1] window around each event, then average across trials
to get a firing-rate estimate per bin.

Endpoint strategy
─────────────────
The service does TWO doc fetches:

  1. ``unit_doc_id`` — the vmspikesummary doc containing the spike
     train. Same extraction path as
     :mod:`backend.services.spike_summary_service` (probes
     ``data.vmspikesummary.spike_times``, ``spiketimes``,
     ``sample_times``); also probes for a separate binary file when
     the JSON body doesn't carry inlined spike times.
  2. ``stimulus_doc_id`` — a stimulus_presentation OR stimulus_response
     doc. Event timestamps live under different paths depending on the
     NDI doc class; we try a few canonical locations in order:
       · ``data.stimulus_presentation.presentations[*].time_started``
       · ``data.stimulus_response.responses[*].stim_time``
       · ``data.events`` (preprocessed top-level array)
       · ``events`` (top-level fallback)

Binning
───────
We build the histogram with ``numpy.histogram`` over the merged set of
relative spike times across all trials. The bin layout is
``np.linspace(t0, t1, N_bins + 1)`` so the centers are deterministic
and the user can re-derive them client-side from ``t0``, ``t1``, and
``bin_size_ms``.

Output caps (hard, server-side):

* ``bin_size_ms >= 1`` (1 ms is the typical fine-grained PSTH bin)
* ``t1 - t0 <= 10`` seconds (PSTH analysis windows >10 s are unusual)
* ``N_bins <= 1000``

These mirror the spike-summary caps in spirit — keep response shapes
bounded so the chart layer doesn't choke and the chat tool can predict
payload size.

Soft-error envelope
───────────────────
The service surfaces problems via ``error`` + ``error_kind`` on the
response object rather than raising:

* ``"decode_failed"`` — unit doc had no parseable spike-times array
* ``"no_events"`` — stimulus doc had no extractable event timestamps
* ``"empty_window"`` — events extracted but every window was empty
  (still returns valid zero-counts arrays so the chart renders)

The router translates the cloud-tier exceptions
(``CloudUnreachable``, ``CloudTimeout``, ``CloudInternalError``) into a
``"cloud_unavailable"`` envelope at the HTTP boundary.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..auth.session import SessionData
from ..observability.logging import get_logger
from .binary_service import BinaryService
from .document_service import DocumentService

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunables — module-level so tests can monkeypatch and so the constants
# are reachable from tests without re-importing internals.
# ---------------------------------------------------------------------------

# Default analysis window (seconds) around each stimulus event.
DEFAULT_T0 = -0.5
DEFAULT_T1 = 1.5

# Default histogram bin width (milliseconds). 20 ms strikes a balance
# between rate-curve smoothness and temporal resolution for typical
# visual / somatosensory stimuli.
DEFAULT_BIN_SIZE_MS = 20.0

# Hard caps. ``bin_size_ms`` floor of 1 ms keeps the bin count bounded
# even for the maximum 10 s window (10000 / 1 = 10000 — we cap further
# at MAX_BINS). The 10-second window is enough for any typical
# stimulus response; longer-window analyses should use a different
# tool.
MIN_BIN_SIZE_MS = 1.0
MAX_WINDOW_SECONDS = 10.0
MAX_BINS = 1000

# Per-trial raster cap. The optional raster underneath the PSTH gets
# one array per trial; we cap the total returned spike count to keep
# the payload bounded. The PSTH histogram itself is computed on the
# UNCAPPED spike set so the rate-curve accuracy is preserved.
MAX_RASTER_SPIKES_TOTAL = 10_000


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class PsthRequest(BaseModel):
    """Input shape for ``POST /api/datasets/{id}/psth``.

    Aliases let the router accept camelCase from the TS chat proxy
    (``unitDocId``, ``stimulusDocId``, etc.) without translation.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    unit_doc_id: str = Field(..., alias="unitDocId", min_length=1)
    stimulus_doc_id: str = Field(..., alias="stimulusDocId", min_length=1)
    t0: float = Field(default=DEFAULT_T0)
    t1: float = Field(default=DEFAULT_T1)
    bin_size_ms: float = Field(default=DEFAULT_BIN_SIZE_MS, alias="binSizeMs")
    include_raster: bool = Field(default=False, alias="includeRaster")
    title: str | None = Field(default=None, max_length=160)


class PsthResponse(BaseModel):
    """Top-level PSTH response.

    ``bin_centers`` and ``counts`` / ``mean_rate_hz`` are parallel
    arrays of length N_bins. ``per_trial_raster`` is included only when
    the request set ``include_raster=True``; it's a list of N_trials
    sublists, each holding the spike times for that trial expressed
    relative to its event onset (i.e. ``spike_time - event_time``,
    bounded to ``[t0, t1]``).

    ``error`` + ``error_kind`` populated for soft failures; consumers
    branch on ``error_kind`` to render a friendly message rather than
    a hard error boundary.
    """

    bin_centers: list[float]
    counts: list[int]
    mean_rate_hz: list[float]
    n_trials: int
    n_spikes: int
    bin_size_ms: float
    t0: float
    t1: float
    unit_name: str
    unit_doc_id: str
    stimulus_doc_id: str
    per_trial_raster: list[list[float]] | None = None
    error: str | None = None
    error_kind: str | None = None


# ---------------------------------------------------------------------------
# Public orchestration entry point
# ---------------------------------------------------------------------------


async def compute_psth(
    request: PsthRequest,
    *,
    document_service: DocumentService,
    binary_service: BinaryService,
    session: SessionData | None,
    dataset_id: str,
) -> PsthResponse:
    """Build a PSTH response for one unit + one stimulus doc.

    Parameters
    ----------
    request:
        Validated PSTH input (see :class:`PsthRequest`).
    document_service:
        Used to fetch the unit + stimulus doc bodies.
    binary_service:
        Used to decode the unit's binary file when spike times aren't
        inlined in the JSON body. The same fallback path the
        spike-summary service uses.
    session:
        Optional session — propagated as ``access_token`` so private
        datasets work for logged-in users.
    dataset_id:
        From the URL path. Source of truth for routing.
    """
    access_token = session.access_token if session else None
    t0, t1, bin_size_ms, validation_error = _validate_window(request)

    # Bail early on validation failure — return a soft envelope rather
    # than raising so the chat tool can surface a friendly explanation.
    if validation_error is not None:
        return _empty_response(
            request,
            unit_name="",
            error=validation_error,
            error_kind="invalid_window",
            t0=t0,
            t1=t1,
            bin_size_ms=bin_size_ms,
        )

    unit_name, spike_times, unit_err = await _resolve_unit(
        request,
        document_service=document_service,
        binary_service=binary_service,
        dataset_id=dataset_id,
        access_token=access_token,
    )
    if unit_err is not None:
        return _empty_response(
            request, unit_name=unit_name, error=unit_err,
            error_kind="decode_failed",
            t0=t0, t1=t1, bin_size_ms=bin_size_ms,
        )

    events, events_err = await _resolve_events(
        request,
        document_service=document_service,
        dataset_id=dataset_id,
        access_token=access_token,
    )
    if events_err is not None:
        return _empty_response(
            request, unit_name=unit_name, error=events_err,
            error_kind="no_events",
            t0=t0, t1=t1, bin_size_ms=bin_size_ms,
        )

    # --- Compute the histogram ---
    bin_edges, bin_centers = _build_bin_arrays(t0, t1, bin_size_ms)
    spike_arr = np.asarray(spike_times, dtype=np.float64)

    all_relative: list[float] = []
    per_trial_raster: list[list[float]] = []
    for event_t in events:
        lo = event_t + t0
        hi = event_t + t1
        # Use boolean mask + slice — numpy.searchsorted would also work
        # but the mask is clearer and the spike arrays are small enough
        # that the extra alloc doesn't matter.
        in_window = spike_arr[(spike_arr >= lo) & (spike_arr <= hi)]
        relatives = (in_window - event_t).tolist()
        all_relative.extend(relatives)
        if request.include_raster:
            per_trial_raster.append(relatives)

    n_trials = len(events)
    bin_size_seconds = bin_size_ms / 1000.0

    if all_relative:
        counts_arr, _ = np.histogram(
            np.asarray(all_relative, dtype=np.float64),
            bins=bin_edges,
        )
    else:
        # Window emptied — still return the zero-counts arrays so the
        # chart renders a flat trace. n_trials is still meaningful
        # (events were found, they just had no spikes near them).
        counts_arr = np.zeros(len(bin_centers), dtype=np.int64)

    counts = [int(c) for c in counts_arr.tolist()]
    # Normalize: counts / (n_trials * bin_size_seconds) gives Hz.
    # Guard against div-by-zero just in case (events list is non-empty
    # here but defensive).
    norm = n_trials * bin_size_seconds
    mean_rate_hz = (
        [c / norm for c in counts] if norm > 0 else [0.0] * len(counts)
    )

    raster_field: list[list[float]] | None = None
    if request.include_raster:
        raster_field = _cap_raster(per_trial_raster, MAX_RASTER_SPIKES_TOTAL)

    error: str | None = None
    error_kind: str | None = None
    if not all_relative:
        # Soft envelope — chart still renders but caller can surface a hint.
        error = (
            f"No spikes fell within the [{t0:.3f}, {t1:.3f}] s window of "
            f"any of the {n_trials} stimulus events"
        )
        error_kind = "empty_window"

    return PsthResponse(
        bin_centers=[float(c) for c in bin_centers.tolist()],
        counts=counts,
        mean_rate_hz=mean_rate_hz,
        n_trials=n_trials,
        n_spikes=len(all_relative),
        bin_size_ms=bin_size_ms,
        t0=t0,
        t1=t1,
        unit_name=unit_name,
        unit_doc_id=request.unit_doc_id,
        stimulus_doc_id=request.stimulus_doc_id,
        per_trial_raster=raster_field,
        error=error,
        error_kind=error_kind,
    )


# ---------------------------------------------------------------------------
# Unit + event resolution — fetch + extract + soft-error mapping
# ---------------------------------------------------------------------------


async def _resolve_unit(
    request: PsthRequest,
    *,
    document_service: DocumentService,
    binary_service: BinaryService,
    dataset_id: str,
    access_token: str | None,
) -> tuple[str, list[float], str | None]:
    """Resolve the unit doc + extract the spike-times array.

    Returns ``(unit_name, spike_times, error_message_or_none)``. Empty
    string + empty list on hard fetch failure; populated tuple +
    error message on extraction failure; empty error_message on
    success.
    """
    try:
        unit_doc = await document_service.detail(
            dataset_id, request.unit_doc_id, access_token=access_token,
        )
    except Exception as exc:
        log.warning(
            "psth.unit_doc_fetch_failed",
            dataset_id=dataset_id,
            unit_doc_id=request.unit_doc_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return "", [], (
            f"Could not fetch unit document {request.unit_doc_id}: {exc}"
        )

    unit_name = _pick_unit_name(unit_doc, request.unit_doc_id)
    spike_times = _extract_spike_times_from_doc(unit_doc)
    if not spike_times:
        # Try the binary-file fallback. Most vmspikesummary docs inline
        # the spike-times array in JSON; some have a separate binary
        # file. We probe the binary path only when JSON extraction
        # returned nothing so the cheap path stays cheap.
        spike_times = await _extract_spike_times_from_binary(
            unit_doc, binary_service, access_token=access_token,
        )
    if not spike_times:
        return unit_name, [], (
            "vmspikesummary doc had no parseable spike_times array "
            "(checked data.vmspikesummary.{spike_times, spiketimes, "
            "sample_times} and binary-file fallback)"
        )
    return unit_name, spike_times, None


async def _resolve_events(
    request: PsthRequest,
    *,
    document_service: DocumentService,
    dataset_id: str,
    access_token: str | None,
) -> tuple[list[float], str | None]:
    """Resolve the stimulus doc + extract its event-time array.

    Returns ``(events, error_message_or_none)``. Empty list + error
    on any soft failure; populated list + None on success.
    """
    try:
        stim_doc = await document_service.detail(
            dataset_id, request.stimulus_doc_id, access_token=access_token,
        )
    except Exception as exc:
        log.warning(
            "psth.stimulus_doc_fetch_failed",
            dataset_id=dataset_id,
            stimulus_doc_id=request.stimulus_doc_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return [], (
            f"Could not fetch stimulus document {request.stimulus_doc_id}: {exc}"
        )

    events = _extract_event_times(stim_doc)
    if not events:
        return [], (
            "stimulus document had no extractable event timestamps "
            "(checked data.stimulus_presentation.presentations[*].time_started, "
            "data.stimulus_response.responses[*].stim_time, "
            "data.events, and top-level events)"
        )
    return events, None


# ---------------------------------------------------------------------------
# Validation + bin layout
# ---------------------------------------------------------------------------


def _validate_window(
    request: PsthRequest,
) -> tuple[float, float, float, str | None]:
    """Validate the [t0, t1] window + bin_size_ms.

    Returns ``(t0, t1, bin_size_ms, error_or_none)``. When the error is
    non-None the caller bails with a soft envelope. The values are
    returned even on failure so the envelope can echo what the caller
    asked for (useful in tests + caller diagnostics).
    """
    t0 = float(request.t0)
    t1 = float(request.t1)
    bin_size_ms = float(request.bin_size_ms)

    if not (np.isfinite(t0) and np.isfinite(t1) and np.isfinite(bin_size_ms)):
        return t0, t1, bin_size_ms, (
            "t0, t1, and bin_size_ms must all be finite numbers"
        )
    if t1 <= t0:
        return t0, t1, bin_size_ms, (
            f"t1 ({t1}) must be greater than t0 ({t0})"
        )
    if (t1 - t0) > MAX_WINDOW_SECONDS:
        return t0, t1, bin_size_ms, (
            f"Window ({t1 - t0:.3f} s) exceeds the maximum allowed "
            f"({MAX_WINDOW_SECONDS} s)"
        )
    if bin_size_ms < MIN_BIN_SIZE_MS:
        return t0, t1, bin_size_ms, (
            f"bin_size_ms ({bin_size_ms}) is below the minimum "
            f"({MIN_BIN_SIZE_MS} ms)"
        )
    # Estimate bin count to enforce the MAX_BINS cap.
    n_bins_est = round((t1 - t0) * 1000.0 / bin_size_ms)
    if n_bins_est > MAX_BINS:
        return t0, t1, bin_size_ms, (
            f"Bin count ({n_bins_est}) exceeds the maximum ({MAX_BINS}); "
            f"increase bin_size_ms or narrow [t0, t1]"
        )
    return t0, t1, bin_size_ms, None


def _build_bin_arrays(
    t0: float, t1: float, bin_size_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(bin_edges, bin_centers)`` for the histogram.

    The bin count is ``round((t1-t0)*1000 / bin_size_ms)`` so the bin
    width matches the request as closely as integer-bin layout allows.
    Edges are ``np.linspace(t0, t1, n_bins+1)``; centers are the
    midpoints.
    """
    n_bins = max(1, round((t1 - t0) * 1000.0 / bin_size_ms))
    edges = np.linspace(t0, t1, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    return edges, centers


# ---------------------------------------------------------------------------
# Doc-body extraction — spike times
# ---------------------------------------------------------------------------


def _extract_spike_times_from_doc(doc: dict[str, Any]) -> list[float] | None:
    """Extract inlined spike times from a vmspikesummary doc's JSON body.

    Mirrors the field-probe order in
    :mod:`backend.services.spike_summary_service` so behaviour stays
    consistent across the two services.

    Returns None when no array of numbers is found at any candidate
    path. Non-numeric entries are skipped silently (matches the TS
    handler); a doc with mixed-type entries returns the numeric subset.
    """
    if not isinstance(doc, dict):
        return None
    data = doc.get("data")
    if not isinstance(data, dict):
        return None
    inner = data.get("vmspikesummary")
    if not isinstance(inner, dict):
        return None
    for key in ("spike_times", "spiketimes", "sample_times"):
        v = inner.get(key)
        if not isinstance(v, list) or not v:
            continue
        nums = _coerce_numeric_list(v)
        if nums:
            return nums
    return None


async def _extract_spike_times_from_binary(
    doc: dict[str, Any],
    binary_service: BinaryService,
    *,
    access_token: str | None,
) -> list[float] | None:
    """Try the binary-file fallback when JSON extraction returned nothing.

    Some vmspikesummary docs carry their spike data as a separate
    binary file; for those, the same :meth:`BinaryService.get_timeseries`
    pipeline used by /signal can produce the channel arrays. We treat
    the first channel's timestamps as the spike times (a single-channel
    binary in this context is canonically a spike-time series).

    Returns None on any soft failure — caller surfaces the
    decode_failed envelope.
    """
    try:
        ts = await binary_service.get_timeseries(doc, access_token=access_token)
    except Exception as exc:
        log.warning(
            "psth.binary_fallback_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None
    if not isinstance(ts, dict) or ts.get("error"):
        return None
    timestamps = ts.get("timestamps")
    if not isinstance(timestamps, list) or not timestamps:
        return None
    return _coerce_numeric_list(timestamps)


def _coerce_numeric_list(values: list[Any]) -> list[float]:
    """Defensive numeric coerce — matches the spike-summary helper."""
    nums: list[float] = []
    for x in values:
        if isinstance(x, bool):
            # bool is a subclass of int; explicitly skip so True/False
            # don't slip through as 1.0/0.0.
            continue
        if isinstance(x, (int, float)):
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
    return nums


def _is_finite(v: float) -> bool:
    """True iff ``v`` is a finite float — NaN/inf rejected. Wraps
    :func:`math.isfinite` so callers can pass either int or float
    without an explicit cast (math.isfinite accepts both).
    """
    return math.isfinite(v)


def _pick_unit_name(doc: dict[str, Any], doc_id: str) -> str:
    """Prefer ``data.vmspikesummary.name``, then top-level ``name``,
    then a synthesized name from the doc ID tail.
    """
    if isinstance(doc, dict):
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
# Doc-body extraction — stimulus event timestamps
# ---------------------------------------------------------------------------


def _extract_event_times(doc: dict[str, Any]) -> list[float]:
    """Extract event timestamps from a stimulus document.

    Probe order (canonical NDI doc-class paths first, preprocessed
    arrays last):

    1. ``data.stimulus_presentation.presentations[*].time_started``
       — the standard ``stimulus_presentation`` doc class. Each entry
       in ``presentations`` represents one trial; ``time_started`` is
       the onset in seconds.
    2. ``data.stimulus_response.responses[*].stim_time``
       — the ``stimulus_response`` doc class. ``stim_time`` is the
       per-trial stimulus onset.
    3. ``data.events`` (list of floats / list of dicts with ``time``
       or ``t``) — preprocessed top-level array.
    4. ``events`` (top-level fallback) — same shape as #3 but at the
       doc root.

    Returns an empty list when no candidate path yields numeric values;
    caller surfaces a ``"no_events"`` envelope. Non-numeric entries are
    silently skipped.
    """
    if not isinstance(doc, dict):
        return []
    data = doc.get("data")

    # Path 1: stimulus_presentation.presentations[*].time_started
    if isinstance(data, dict):
        sp = data.get("stimulus_presentation")
        if isinstance(sp, dict):
            presentations = sp.get("presentations")
            times = _times_from_event_list(presentations, ("time_started", "time", "t"))
            if times:
                return times

        # Path 2: stimulus_response.responses[*].stim_time
        sr = data.get("stimulus_response")
        if isinstance(sr, dict):
            responses = sr.get("responses")
            times = _times_from_event_list(responses, ("stim_time", "time", "t"))
            if times:
                return times

        # Path 3: data.events (preprocessed; can be list-of-floats or list-of-dicts)
        ev = data.get("events")
        times = _times_from_event_list(ev, ("time", "t", "time_started", "stim_time"))
        if times:
            return times

    # Path 4: top-level events fallback
    top_ev = doc.get("events")
    times = _times_from_event_list(top_ev, ("time", "t", "time_started", "stim_time"))
    if times:
        return times

    return []


def _times_from_event_list(
    items: Any,
    keys: tuple[str, ...],
) -> list[float]:
    """Walk an events-style list, extracting numeric timestamps.

    Accepts either:
      - ``list[float|int]`` — raw timestamps; coerce + filter finite.
      - ``list[dict]`` — each entry contributes the value at the first
        present key in ``keys``.

    Returns an empty list when ``items`` is not a list or yields no
    numerics.
    """
    if not isinstance(items, list) or not items:
        return []
    out: list[float] = []
    for entry in items:
        if isinstance(entry, dict):
            v = _first_numeric_from_dict(entry, keys)
        else:
            v = _coerce_scalar(entry)
        if v is not None:
            out.append(v)
    return out


def _coerce_scalar(entry: Any) -> float | None:
    """Coerce a scalar entry to a finite float; return None when not numeric.

    ``bool`` is rejected explicitly (subclass of int in Python). Strings
    parseable as floats are accepted so doc bodies that round-trip
    through JSON-as-strings still work.
    """
    if isinstance(entry, bool):
        return None
    if isinstance(entry, (int, float)):
        fx = float(entry)
        return fx if _is_finite(fx) else None
    if isinstance(entry, str):
        try:
            parsed = float(entry)
        except (TypeError, ValueError):
            return None
        return parsed if _is_finite(parsed) else None
    return None


def _first_numeric_from_dict(
    entry: dict[str, Any], keys: tuple[str, ...],
) -> float | None:
    """Return the first key in ``keys`` whose value coerces to a finite
    float, or None when nothing matched.
    """
    for key in keys:
        v = _coerce_scalar(entry.get(key))
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _empty_response(
    request: PsthRequest,
    *,
    unit_name: str,
    error: str,
    error_kind: str,
    t0: float,
    t1: float,
    bin_size_ms: float,
) -> PsthResponse:
    """Build a soft-error PsthResponse with empty histogram arrays.

    We still return valid (zero-length) bin arrays so the chart layer
    can render a clean empty state without branching on response
    shape. The ``error`` + ``error_kind`` carry the diagnostic.
    """
    return PsthResponse(
        bin_centers=[],
        counts=[],
        mean_rate_hz=[],
        n_trials=0,
        n_spikes=0,
        bin_size_ms=bin_size_ms,
        t0=t0,
        t1=t1,
        unit_name=unit_name,
        unit_doc_id=request.unit_doc_id,
        stimulus_doc_id=request.stimulus_doc_id,
        per_trial_raster=None,
        error=error,
        error_kind=error_kind,
    )


def _cap_raster(
    per_trial: list[list[float]],
    total_cap: int,
) -> list[list[float]]:
    """Cap the total spike count across the per-trial raster.

    If the raw raster is already under the cap we return it verbatim.
    Otherwise we stride-sample each trial proportionally so the trial
    structure is preserved (callers branch on ``len(per_trial_raster)``
    for the trial count).

    The cap is total spikes across ALL trials, not per-trial. A 50-
    trial recording with 1000 spikes/trial = 50k total → over the
    default 10k cap → each trial gets stride-sampled to ~200 spikes.
    """
    total = sum(len(t) for t in per_trial)
    if total <= total_cap:
        return per_trial
    if total == 0:
        return per_trial
    ratio = total_cap / total
    out: list[list[float]] = []
    for trial in per_trial:
        n = len(trial)
        if n == 0:
            out.append([])
            continue
        keep = max(1, int(n * ratio))
        if keep >= n:
            out.append(list(trial))
            continue
        # Stride-sample preserving first + last so the trial's onset
        # + offset spikes survive.
        if keep <= 2:
            out.append([trial[0], trial[-1]][:keep])
            continue
        step = (n - 1) / (keep - 1)
        seen: set[int] = set()
        picked: list[float] = []
        for i in range(keep):
            idx = round(i * step)
            if idx in seen:
                continue
            seen.add(idx)
            picked.append(trial[idx])
        out.append(picked)
    return out
