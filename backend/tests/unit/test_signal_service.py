"""signal_service.py — LTTB downsampling + time-window trimming.

Tests verify:
  - LTTB preserves first + last points
  - Threshold ≥ input length returns input unchanged
  - Threshold < 3 returns input unchanged (degenerate case)
  - Time-window trim slices both timestamps and channels in sync
  - Empty / error payloads are passed through with the right envelope
  - Multi-channel downsampling uses ONE kept-index set across channels
    so the time axis stays consistent
"""
from __future__ import annotations

from backend.services.signal_service import (
    DEFAULT_DOWNSAMPLE_POINTS,
    downsample_timeseries,
    lttb_downsample,
)


class TestLttbDownsample:
    def test_returns_input_when_smaller_than_threshold(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        out, kept = lttb_downsample(values, threshold=10)
        assert out == values
        assert kept == [0, 1, 2, 3]

    def test_returns_input_when_threshold_too_small(self) -> None:
        values = list(map(float, range(100)))
        out, kept = lttb_downsample(values, threshold=2)
        assert out == values
        assert kept == list(range(100))

    def test_preserves_first_and_last_points(self) -> None:
        values = [float(x) for x in range(1000)]
        out, kept = lttb_downsample(values, threshold=50)
        assert len(out) == 50
        assert kept[0] == 0
        assert kept[-1] == 999
        assert out[0] == 0.0
        assert out[-1] == 999.0

    def test_picks_spike_in_a_bucket(self) -> None:
        # Construct a long flat signal with one big spike at index 500.
        values = [0.0] * 1000
        values[500] = 100.0
        _, kept = lttb_downsample(values, threshold=20)
        # The bucket containing index 500 should select that spike (it
        # maximises triangle area against the adjacent flat regions).
        assert 500 in kept

    def test_treats_none_as_zero_for_area_but_preserves_kept_value(self) -> None:
        values: list[float | None] = [None, 1.0, None, 2.0, None]
        out, _ = lttb_downsample(values, threshold=10)
        # Pass-through when smaller than threshold — Nones stay None.
        assert out == values


class TestDownsampleTimeseries:
    def _ts(self, n: int) -> dict[str, object]:
        return {
            "channels": {"ch0": [float(i) for i in range(n)]},
            "timestamps": [i * 0.001 for i in range(n)],  # 1 kHz
            "sample_count": n,
            "format": "nbf",
            "error": None,
        }

    def test_passes_through_when_below_threshold(self) -> None:
        result = downsample_timeseries(self._ts(100), target_points=DEFAULT_DOWNSAMPLE_POINTS, t0_seconds=None, t1_seconds=None)
        assert result["downsampled"] is False
        assert result["original_sample_count"] == 100
        assert result["sample_count"] == 100

    def test_downsamples_when_above_threshold(self) -> None:
        result = downsample_timeseries(self._ts(10_000), target_points=500, t0_seconds=None, t1_seconds=None)
        assert result["downsampled"] is True
        assert result["original_sample_count"] == 10_000
        assert result["sample_count"] == 500

    def test_trims_to_time_window_before_downsampling(self) -> None:
        # 10s of 1 kHz data — trim to [2.0, 4.0] = 2000 samples.
        result = downsample_timeseries(self._ts(10_000), target_points=DEFAULT_DOWNSAMPLE_POINTS, t0_seconds=2.0, t1_seconds=4.0)
        # Time range is bounded by the trim window.
        assert 2.0 <= result["t0_seconds"] <= 2.001
        assert 3.999 <= result["t1_seconds"] <= 4.001
        # Original sample count is the FULL untrimmed length, by design.
        assert result["original_sample_count"] == 10_000

    def test_empty_window_returns_empty_payload(self) -> None:
        result = downsample_timeseries(self._ts(100), target_points=10, t0_seconds=99.0, t1_seconds=100.0)
        assert result["sample_count"] == 0
        assert result["channels"]["ch0"] == []
        assert result["original_sample_count"] == 100

    def test_passes_through_error_payload_unchanged(self) -> None:
        err = {
            "channels": {},
            "timestamps": None,
            "sample_count": 0,
            "format": "",
            "error": "decoder failed",
            "errorKind": "decode",
        }
        result = downsample_timeseries(err, target_points=500, t0_seconds=None, t1_seconds=None)
        assert result["error"] == "decoder failed"
        assert result["downsampled"] is False

    def test_caps_threshold_at_max(self) -> None:
        # Even if caller asks for 100k points, the cap is 5k.
        result = downsample_timeseries(self._ts(100_000), target_points=999_999, t0_seconds=None, t1_seconds=None)
        assert result["sample_count"] <= 5_000

    def test_multi_channel_uses_single_kept_index_set(self) -> None:
        ts = {
            "channels": {
                "vm": [float(i) for i in range(1000)],
                "i": [float(-i) for i in range(1000)],
            },
            "timestamps": [i * 0.001 for i in range(1000)],
            "sample_count": 1000,
            "format": "nbf",
            "error": None,
        }
        result = downsample_timeseries(ts, target_points=50, t0_seconds=None, t1_seconds=None)
        assert len(result["channels"]["vm"]) == len(result["channels"]["i"])
        assert len(result["channels"]["vm"]) == len(result["timestamps"])
