"""Unit tests for psth_service — peri-stimulus time histogram orchestration.

Tests verify:

  * happy path: unit with deterministic spike train + stimulus with
    multiple events produces bin_centers + counts arrays of consistent
    length; mean_rate_hz normalization is correct
  * empty path: zero spikes in window → zero-counts but valid bin
    arrays + correct n_trials
  * include_raster: per-trial relative-time arrays are surfaced
  * cap enforcement: bin_size_ms < 1 ms → invalid_window envelope
  * window cap: (t1 - t0) > 10 s → invalid_window envelope
  * bin-count cap: too many bins → invalid_window envelope
  * soft error: stimulus doc lacks event timestamps → no_events envelope
  * soft error: spike doc binary fails to decode → decode_failed envelope
  * event-extraction across NDI doc-class paths:
      - stimulus_presentation.presentations[*].time_started
      - stimulus_response.responses[*].stim_time
      - data.events (top-level array)
"""
from __future__ import annotations

from typing import Any

from backend.services.psth_service import (
    DEFAULT_BIN_SIZE_MS,
    DEFAULT_T0,
    DEFAULT_T1,
    MAX_WINDOW_SECONDS,
    MIN_BIN_SIZE_MS,
    PsthRequest,
    _build_bin_arrays,
    _cap_raster,
    _extract_event_times,
    _extract_spike_times_from_doc,
    _validate_window,
    compute_psth,
)

# ---------------------------------------------------------------------------
# Fakes — mirror the shape the real services produce. The real
# document_service.detail() returns a normalized doc dict; we hand
# back canned dicts keyed by doc_id so the test orchestrator picks
# the right body per call.
# ---------------------------------------------------------------------------


class _FakeDocumentService:
    """Stub for DocumentService.detail — canned responses per doc_id.

    The real signature is ``detail(dataset_id, document_id, *,
    access_token)``; we mirror that here so the orchestrator's call
    sites don't have to branch on test vs prod.
    """

    def __init__(self, docs_by_id: dict[str, dict[str, Any]]) -> None:
        self._docs = docs_by_id
        self.calls: list[tuple[str, str]] = []

    async def detail(
        self,
        dataset_id: str,
        document_id: str,
        *,
        access_token: str | None,  # noqa: ARG002 — stub signature
    ) -> dict[str, Any]:
        self.calls.append((dataset_id, document_id))
        if document_id not in self._docs:
            raise RuntimeError(f"no canned doc for {document_id}")
        return self._docs[document_id]


class _FakeBinaryService:
    """Stub for BinaryService.get_timeseries — canned response.

    Used for the binary-fallback path when the unit doc's JSON body
    has no inlined spike-times array. The real service decodes
    NBF/VHSB; tests use a pre-baked dict instead.
    """

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {"timestamps": [], "channels": {}, "error": "no_data"}
        self.calls: list[dict[str, Any]] = []

    async def get_timeseries(
        self,
        document: dict[str, Any],
        *,
        access_token: str | None,  # noqa: ARG002 — stub signature
        filename: str | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.calls.append(document)
        return self.response


def _vmspikesummary_doc(
    *,
    name: str = "unit_001",
    spike_times: list[float] | None = None,
) -> dict[str, Any]:
    """Build a minimal vmspikesummary doc body. Spike times inline
    under ``data.vmspikesummary.spike_times``.
    """
    inner: dict[str, Any] = {"name": name}
    if spike_times is not None:
        inner["spike_times"] = spike_times
    return {
        "id": "u" * 24,
        "data": {"vmspikesummary": inner},
    }


def _stim_presentation_doc(times: list[float]) -> dict[str, Any]:
    """Stimulus doc using the ``stimulus_presentation`` schema."""
    return {
        "id": "s" * 24,
        "data": {
            "stimulus_presentation": {
                "presentations": [{"time_started": t} for t in times],
            },
        },
    }


def _stim_response_doc(times: list[float]) -> dict[str, Any]:
    """Stimulus doc using the ``stimulus_response`` schema."""
    return {
        "id": "s" * 24,
        "data": {
            "stimulus_response": {
                "responses": [{"stim_time": t} for t in times],
            },
        },
    }


def _stim_events_doc(times: list[float]) -> dict[str, Any]:
    """Stimulus doc with preprocessed top-level events array."""
    return {
        "id": "s" * 24,
        "data": {"events": list(times)},
    }


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


class TestExtractSpikeTimesFromDoc:
    def test_pulls_spike_times_under_canonical_key(self) -> None:
        doc = _vmspikesummary_doc(spike_times=[0.1, 0.2, 0.3])
        out = _extract_spike_times_from_doc(doc)
        assert out == [0.1, 0.2, 0.3]

    def test_falls_back_to_alternate_keys(self) -> None:
        doc = {"data": {"vmspikesummary": {"sample_times": [1.0, 2.0]}}}
        out = _extract_spike_times_from_doc(doc)
        assert out == [1.0, 2.0]

    def test_returns_none_when_no_array(self) -> None:
        doc = {"data": {"vmspikesummary": {"name": "u"}}}
        assert _extract_spike_times_from_doc(doc) is None

    def test_skips_non_numeric_entries(self) -> None:
        doc = {"data": {"vmspikesummary": {"spike_times": [1.0, "bad", 2.0, None, True, False]}}}
        out = _extract_spike_times_from_doc(doc)
        # bool/None excluded; numeric strings accepted by _coerce_numeric_list
        assert out == [1.0, 2.0]


class TestExtractEventTimes:
    def test_stimulus_presentation_path(self) -> None:
        doc = _stim_presentation_doc([1.0, 2.0, 3.0])
        assert _extract_event_times(doc) == [1.0, 2.0, 3.0]

    def test_stimulus_response_path(self) -> None:
        doc = _stim_response_doc([0.5, 1.5])
        assert _extract_event_times(doc) == [0.5, 1.5]

    def test_data_events_array(self) -> None:
        doc = _stim_events_doc([10.0, 20.0])
        assert _extract_event_times(doc) == [10.0, 20.0]

    def test_data_events_list_of_dicts(self) -> None:
        doc = {"data": {"events": [{"time": 1.0}, {"t": 2.0}]}}
        assert _extract_event_times(doc) == [1.0, 2.0]

    def test_top_level_events_fallback(self) -> None:
        doc = {"events": [5.0, 6.0]}
        assert _extract_event_times(doc) == [5.0, 6.0]

    def test_returns_empty_when_no_path_matches(self) -> None:
        assert _extract_event_times({"data": {}}) == []
        assert _extract_event_times({}) == []
        # Wrong-shape entries (no recognized key)
        assert _extract_event_times({"data": {"events": [{"foo": 1.0}]}}) == []


class TestValidateWindow:
    def _req(self, **kwargs: Any) -> PsthRequest:
        defaults: dict[str, Any] = {
            "unit_doc_id": "u" * 24,
            "stimulus_doc_id": "s" * 24,
            "t0": DEFAULT_T0,
            "t1": DEFAULT_T1,
            "bin_size_ms": DEFAULT_BIN_SIZE_MS,
        }
        defaults.update(kwargs)
        return PsthRequest(**defaults)

    def test_defaults_are_valid(self) -> None:
        _, _, _, err = _validate_window(self._req())
        assert err is None

    def test_bin_size_below_minimum_rejected(self) -> None:
        _, _, _, err = _validate_window(self._req(bin_size_ms=0.5))
        assert err is not None
        assert "minimum" in err.lower()

    def test_window_too_wide_rejected(self) -> None:
        _, _, _, err = _validate_window(
            self._req(t0=-5.0, t1=5.0 + MAX_WINDOW_SECONDS),
        )
        assert err is not None
        assert "window" in err.lower() or "exceeds" in err.lower()

    def test_t1_must_exceed_t0(self) -> None:
        _, _, _, err = _validate_window(self._req(t0=1.0, t1=0.5))
        assert err is not None

    def test_too_many_bins_rejected(self) -> None:
        # (1 s) / (0.5 ms) = 2000 bins → over MAX_BINS=1000. But 0.5 ms
        # fails the bin_size_ms floor first, so we need a wider window.
        # 2 s / 1 ms = 2000 bins → over cap.
        _, _, _, err = _validate_window(
            self._req(t0=0.0, t1=2.0, bin_size_ms=MIN_BIN_SIZE_MS),
        )
        assert err is not None
        assert "bin" in err.lower()


class TestBuildBinArrays:
    def test_bin_count_matches_window_and_size(self) -> None:
        edges, centers = _build_bin_arrays(t0=0.0, t1=1.0, bin_size_ms=10.0)
        # 1 s / 10 ms = 100 bins
        assert len(centers) == 100
        assert len(edges) == 101

    def test_centers_are_midpoints(self) -> None:
        _, centers = _build_bin_arrays(t0=0.0, t1=1.0, bin_size_ms=100.0)
        # 10 bins, centers at 0.05, 0.15, ..., 0.95
        assert abs(centers[0] - 0.05) < 1e-9
        assert abs(centers[-1] - 0.95) < 1e-9


class TestCapRaster:
    def test_under_cap_returns_verbatim(self) -> None:
        per_trial = [[1.0, 2.0], [3.0]]
        out = _cap_raster(per_trial, total_cap=100)
        assert out == per_trial

    def test_over_cap_strides_proportionally(self) -> None:
        # 3 trials with 100 spikes each = 300 total; cap at 30 → ratio 0.1
        per_trial = [[float(i) for i in range(100)] for _ in range(3)]
        out = _cap_raster(per_trial, total_cap=30)
        # Each trial gets ~10 spikes (max keep), preserving endpoints
        assert len(out) == 3
        for trial in out:
            assert len(trial) <= 11
            assert trial[0] == 0.0
            assert trial[-1] == 99.0


# ---------------------------------------------------------------------------
# compute_psth integration — service-level happy paths + soft errors
# ---------------------------------------------------------------------------


def _spike_train(n: int, stride: float = 0.01) -> list[float]:
    """Build a deterministic spike train of n spikes at stride seconds."""
    return [i * stride for i in range(n)]


def _req(**kwargs: Any) -> PsthRequest:
    defaults: dict[str, Any] = {
        "unit_doc_id": "a" * 24,
        "stimulus_doc_id": "b" * 24,
    }
    defaults.update(kwargs)
    return PsthRequest(**defaults)


async def test_happy_path_consistent_arrays() -> None:
    """Unit with 100 spikes + stimulus with 10 events.

    Each event sits inside the spike train so the [-0.5, 1.5] window
    captures spikes around it. Verifies:
      - bin_centers, counts, mean_rate_hz are parallel arrays
      - n_trials matches the event count
      - n_spikes is non-zero
      - error/error_kind are None on the happy path
    """
    # Unit: 100 spikes spaced 0.01 s apart, covering [0, 1] s
    spike_times = _spike_train(100, stride=0.01)
    unit_doc = _vmspikesummary_doc(spike_times=spike_times)
    # Stimulus: 10 events at 0.05, 0.10, ..., 0.50 s — every event has
    # spikes both before (t0 = -0.5) and after (t1 = 1.5) it.
    stim_doc = _stim_presentation_doc([0.05 + i * 0.05 for i in range(10)])

    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )

    # Parallel-array consistency.
    n_bins = len(resp.bin_centers)
    assert n_bins > 0
    assert len(resp.counts) == n_bins
    assert len(resp.mean_rate_hz) == n_bins

    # Default bin layout: (-0.5, 1.5) s @ 20 ms = 100 bins.
    assert n_bins == 100

    # Trials + spikes were counted.
    assert resp.n_trials == 10
    assert resp.n_spikes > 0
    assert resp.error is None
    assert resp.error_kind is None

    # Rate normalization sanity: mean_rate_hz[i] == counts[i] / (n_trials * bin_size_s)
    bin_size_s = resp.bin_size_ms / 1000.0
    for c, r in zip(resp.counts, resp.mean_rate_hz, strict=True):
        assert abs(r - c / (resp.n_trials * bin_size_s)) < 1e-9


async def test_empty_window_returns_zero_counts_envelope() -> None:
    """Events present, but their windows contain zero spikes.

    Spike train is far from the events; n_trials still matches event
    count; counts are all zero; valid bin arrays returned;
    error_kind='empty_window'.
    """
    # Unit: spikes at t = 100, 100.01, ... (way after the events)
    spike_times = [100.0 + i * 0.01 for i in range(20)]
    unit_doc = _vmspikesummary_doc(spike_times=spike_times)
    # Stimulus events at t = 0, 1, 2 — windows are all near zero;
    # no spikes overlap.
    stim_doc = _stim_presentation_doc([0.0, 1.0, 2.0])

    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )

    assert resp.n_trials == 3
    assert resp.n_spikes == 0
    assert resp.error_kind == "empty_window"
    # Bin arrays are still populated (chart can render flat trace).
    assert len(resp.bin_centers) > 0
    assert len(resp.counts) == len(resp.bin_centers)
    assert all(c == 0 for c in resp.counts)
    assert all(r == 0.0 for r in resp.mean_rate_hz)


async def test_include_raster_returns_per_trial_arrays() -> None:
    """include_raster=True surfaces per-trial relative spike times."""
    spike_times = _spike_train(50, stride=0.02)
    unit_doc = _vmspikesummary_doc(spike_times=spike_times)
    stim_doc = _stim_presentation_doc([0.0, 0.5])

    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(include_raster=True),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )

    assert resp.per_trial_raster is not None
    assert len(resp.per_trial_raster) == 2  # two events
    # Every value must fall within [t0, t1] (relative-time bounds)
    for trial in resp.per_trial_raster:
        for t in trial:
            assert resp.t0 <= t <= resp.t1


async def test_raster_default_off() -> None:
    """include_raster defaults to False → per_trial_raster=None."""
    unit_doc = _vmspikesummary_doc(spike_times=_spike_train(10))
    stim_doc = _stim_presentation_doc([0.0])
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.per_trial_raster is None


async def test_cap_enforcement_rejects_tiny_bins() -> None:
    """bin_size_ms below MIN_BIN_SIZE_MS surfaces invalid_window envelope.

    The service returns a soft envelope rather than raising so the
    chat tool can render the explanation. n_trials=0 because we never
    fetched events.
    """
    unit_doc = _vmspikesummary_doc(spike_times=_spike_train(10))
    stim_doc = _stim_presentation_doc([0.0])
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(bin_size_ms=0.5),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.error_kind == "invalid_window"
    assert resp.n_trials == 0
    assert resp.bin_centers == []


async def test_soft_error_no_event_timestamps() -> None:
    """Stimulus doc with no extractable timestamps → no_events envelope."""
    unit_doc = _vmspikesummary_doc(spike_times=_spike_train(10))
    # Stimulus doc shape that doesn't match any extraction path.
    stim_doc = {"data": {"stimulus_presentation": {"name": "no events here"}}}
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.error_kind == "no_events"
    # Diagnostics: caller can echo which doc failed
    assert resp.stimulus_doc_id == "b" * 24


async def test_soft_error_decode_failed_when_no_spike_times() -> None:
    """Unit doc with no inlined spike times + binary fallback empty →
    decode_failed envelope.
    """
    unit_doc = _vmspikesummary_doc(spike_times=None)  # no spike_times key
    stim_doc = _stim_presentation_doc([0.0, 1.0])
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService(
        response={"timestamps": [], "channels": {}, "error": "no_file"},
    )
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.error_kind == "decode_failed"
    assert resp.n_trials == 0


async def test_binary_fallback_supplies_spike_times() -> None:
    """When the JSON body has no spike_times, the binary fallback's
    timestamps array is used as the spike train.
    """
    unit_doc = _vmspikesummary_doc(spike_times=None)
    stim_doc = _stim_presentation_doc([0.0])
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    # Binary returns 5 timestamps within the [-0.5, 1.5] window.
    bs = _FakeBinaryService(
        response={
            "timestamps": [0.1, 0.2, 0.3, 0.4, 0.5],
            "channels": {"ch0": [1.0] * 5},
            "error": None,
        },
    )
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.error_kind is None
    assert resp.n_spikes == 5
    assert resp.n_trials == 1


async def test_stimulus_response_path_works_end_to_end() -> None:
    """Verifies the stimulus_response.responses[*].stim_time path."""
    unit_doc = _vmspikesummary_doc(spike_times=_spike_train(50))
    stim_doc = _stim_response_doc([0.1, 0.2, 0.3])
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.n_trials == 3
    assert resp.error_kind is None


async def test_unit_name_extracted_from_vmspikesummary_name() -> None:
    """unit_name is propagated from data.vmspikesummary.name."""
    unit_doc = _vmspikesummary_doc(name="MUA_ch3_unit5", spike_times=[0.1])
    stim_doc = _stim_presentation_doc([0.0])
    docs = _FakeDocumentService({"a" * 24: unit_doc, "b" * 24: stim_doc})
    bs = _FakeBinaryService()
    resp = await compute_psth(
        _req(),
        document_service=docs,  # type: ignore[arg-type]
        binary_service=bs,  # type: ignore[arg-type]
        session=None,
        dataset_id="ds_test",
    )
    assert resp.unit_name == "MUA_ch3_unit5"


async def test_camelcase_alias_accepted() -> None:
    """PsthRequest accepts camelCase aliases from the TS chat proxy."""
    req = PsthRequest.model_validate({
        "unitDocId": "u" * 24,
        "stimulusDocId": "s" * 24,
        "binSizeMs": 50,
        "includeRaster": True,
    })
    assert req.unit_doc_id == "u" * 24
    assert req.stimulus_doc_id == "s" * 24
    assert req.bin_size_ms == 50
    assert req.include_raster is True
