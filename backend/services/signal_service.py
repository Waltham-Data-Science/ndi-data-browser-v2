"""Signal downsampling for the experimental /ask chat's fetch_signal tool.

The chatbot calls ``GET /api/datasets/{id}/documents/{docId}/signal``
which reuses :class:`BinaryService.get_timeseries` to decode the
underlying binary, then trims + downsamples the timeseries to a
chat-friendly size (a 1-hour @ 10 kHz recording is 36 M samples;
streaming that to a chart is unhelpful).

The downsampler is Largest-Triangle-Three-Buckets (LTTB) — a
visualization-preserving algorithm that retains the visual character
of the trace (peaks, troughs, ramps) with a fixed output point count.
LTTB is well-suited for chart rendering because it minimises the
area-difference between buckets, so the rendered shape closely
matches the original at any zoom level.

Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
Representation" (MSc thesis, U. Iceland, 2013).
"""
from __future__ import annotations

from typing import Any

# ----------------------------------------------------------------------------
# Output caps. The bigger of these two limits is the *effective* cap on a
# response: a chart with 5,000 points is already overkill at typical screen
# resolutions, and the JSON payload at 5k channels is ~80 KB per channel.
# These constants are intentionally module-level so tests can monkeypatch.

DEFAULT_DOWNSAMPLE_POINTS = 2_000
MAX_DOWNSAMPLE_POINTS = 5_000


def lttb_downsample(values: list[float | None], threshold: int) -> tuple[list[float | None], list[int]]:
    """Largest-Triangle-Three-Buckets downsample.

    Returns a tuple of (downsampled_values, kept_indices). ``kept_indices`` is
    a sparse list of the original-array indices that survived — caller uses
    these to slice into the timestamps array so the time axis stays aligned.

    The algorithm needs numeric input; ``None`` entries in ``values`` are
    treated as 0 for triangle-area computation (they preserve sample-position
    but don't contribute to peak detection). Callers should hand back the
    raw ``None`` in the output for any kept index — see the ``raw`` slice
    in the router. (Why not strip Nones first? That would shift positions
    and the time alignment would diverge from the parsed timestamps array.)
    """
    n = len(values)
    if n <= threshold or threshold < 3:
        # Nothing to do (or threshold too small to bucket meaningfully).
        return list(values), list(range(n))

    # First + last always kept.
    sampled_indices: list[int] = [0]
    bucket_size = (n - 2) / (threshold - 2)

    a_idx = 0  # index of last selected point

    def _v(i: int) -> float:
        v = values[i]
        return float(v) if v is not None else 0.0

    for i in range(threshold - 2):
        # Average of the next bucket — used to position the third triangle
        # vertex (a, b, c) where b is from THIS bucket and c is the next-
        # bucket average.
        next_start = int((i + 1) * bucket_size) + 1
        next_end = min(int((i + 2) * bucket_size) + 1, n)
        if next_end <= next_start:
            next_end = next_start + 1
        avg_x = (next_start + next_end - 1) / 2.0
        avg_y = sum(_v(j) for j in range(next_start, next_end)) / max(1, next_end - next_start)

        # Search THIS bucket for the point that maximises triangle area
        # with (a_idx, candidate, avg_next).
        bucket_start = int(i * bucket_size) + 1
        bucket_end = min(int((i + 1) * bucket_size) + 1, n)
        a_x = a_idx
        a_y = _v(a_idx)

        max_area = -1.0
        best_idx = bucket_start
        for j in range(bucket_start, bucket_end):
            # 2 × triangle area
            area = abs((a_x - avg_x) * (_v(j) - a_y) - (a_x - j) * (avg_y - a_y))
            if area > max_area:
                max_area = area
                best_idx = j
        sampled_indices.append(best_idx)
        a_idx = best_idx

    sampled_indices.append(n - 1)
    sampled_values = [values[i] for i in sampled_indices]
    return sampled_values, sampled_indices


def downsample_timeseries(
    timeseries: dict[str, Any],
    target_points: int,
    t0_seconds: float | None,
    t1_seconds: float | None,
) -> dict[str, Any]:
    """Trim and downsample a TimeseriesData payload for chatbot consumption.

    Input shape matches :func:`BinaryService.get_timeseries`:
    ``{channels: {name: [vals]}, timestamps: [t], sample_count: N, format}``.

    Output shape adds:
      - ``downsampled``: True if any reduction occurred
      - ``original_sample_count``: pre-downsample length
      - ``t0_seconds``, ``t1_seconds``: trim-window actually applied
    """
    if timeseries.get("error"):
        # Pass error payloads through unchanged so the router can surface
        # the upstream message to the chatbot.
        return {**timeseries, "downsampled": False, "original_sample_count": timeseries.get("sample_count", 0)}

    channels: dict[str, list[float | None]] = timeseries.get("channels") or {}
    timestamps: list[float] | None = timeseries.get("timestamps")
    if timestamps is None or not channels:
        return {
            **timeseries,
            "downsampled": False,
            "original_sample_count": timeseries.get("sample_count", 0),
        }

    original_n = len(timestamps)

    # Trim by time window first — saves work in the downsampler.
    start_idx = 0
    end_idx = original_n
    if t0_seconds is not None:
        # Find first index with timestamp >= t0
        for i, t in enumerate(timestamps):
            if t >= t0_seconds:
                start_idx = i
                break
        else:
            start_idx = original_n
    if t1_seconds is not None:
        # Find first index with timestamp > t1; we want indices BEFORE this.
        for i in range(start_idx, original_n):
            if timestamps[i] > t1_seconds:
                end_idx = i
                break
        else:
            end_idx = original_n

    if end_idx <= start_idx:
        # Empty window — return an empty response shape rather than erroring.
        return {
            "channels": {name: [] for name in channels},
            "timestamps": [],
            "sample_count": 0,
            "format": timeseries.get("format", ""),
            "error": None,
            "downsampled": False,
            "original_sample_count": original_n,
        }

    sliced_ts = timestamps[start_idx:end_idx]
    sliced_channels = {name: list(values[start_idx:end_idx]) for name, values in channels.items()}
    sliced_n = len(sliced_ts)

    # Bound the threshold.
    threshold = max(3, min(target_points, MAX_DOWNSAMPLE_POINTS))

    if sliced_n <= threshold:
        return {
            "channels": sliced_channels,
            "timestamps": sliced_ts,
            "sample_count": sliced_n,
            "format": timeseries.get("format", ""),
            "error": None,
            "downsampled": False,
            "original_sample_count": original_n,
            "t0_seconds": sliced_ts[0] if sliced_ts else None,
            "t1_seconds": sliced_ts[-1] if sliced_ts else None,
        }

    # Downsample every channel using the SAME kept-index set so the time
    # axis stays consistent across channels. We seed from the first channel's
    # values; subsequent channels just re-index.
    first_channel_name = next(iter(sliced_channels))
    _, kept = lttb_downsample(sliced_channels[first_channel_name], threshold)

    down_channels = {
        name: [values[i] for i in kept] for name, values in sliced_channels.items()
    }
    down_ts = [sliced_ts[i] for i in kept]

    return {
        "channels": down_channels,
        "timestamps": down_ts,
        "sample_count": len(down_ts),
        "format": timeseries.get("format", ""),
        "error": None,
        "downsampled": True,
        "original_sample_count": original_n,
        "t0_seconds": down_ts[0] if down_ts else None,
        "t1_seconds": down_ts[-1] if down_ts else None,
    }
