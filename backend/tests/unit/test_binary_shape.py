"""v1-compatible TimeseriesData shape + file-ref extraction for binary_service.

Plan §M5 backend step 1: the ported v1 TimeseriesChart reads
`{channels: Record<string, (number|null)[]>, timestamps?, sample_count,
format, error?}`. Pin that contract so future backend edits can't quietly
regress to the old `{y, sampleRate, nSamples}` shape.
"""
from __future__ import annotations

import struct

import numpy as np
import pytest

from backend.errors import ValidationFailed
from backend.services.binary_service import (
    MAX_FITCURVE_SAMPLES,
    BinaryService,
    _file_info_to_ref,
    _file_refs,
    _parse_nbf,
    _parse_vhsb,
    _timeseries_error,
    _to_nullable_list,
    _ts_shape_single_channel,
)


class TestFileRefs:
    def test_handles_dict_file_info_with_dict_locations(self) -> None:
        """The common element_epoch shape on Haley/VH."""
        doc = {
            "data": {
                "files": {
                    "file_list": ["epoch_binary_data.vhsb"],
                    "file_info": {
                        "name": "epoch_binary_data.vhsb",
                        "locations": {
                            "location": "https://ndi-data.s3.us-east-1.amazonaws.com/signed",
                            "location_type": "url",
                        },
                    },
                },
            },
        }
        refs = _file_refs(doc)
        assert len(refs) == 1
        assert refs[0].url.startswith("https://ndi-data.s3")
        assert refs[0].filename == "epoch_binary_data.vhsb"

    def test_handles_list_file_info(self) -> None:
        doc = {
            "data": {
                "files": {
                    "file_info": [
                        {"name": "a.nbf", "locations": {"location": "https://a.example"}},
                        {"name": "b.nbf", "locations": {"location": "https://b.example"}},
                    ],
                },
            },
        }
        refs = _file_refs(doc)
        assert len(refs) == 2
        assert refs[0].filename == "a.nbf"
        assert refs[1].filename == "b.nbf"

    def test_handles_locations_as_list(self) -> None:
        """Some legacy docs store `locations` as a list of objects."""
        fi = {
            "name": "x.nbf",
            "locations": [{"location": "https://x.example/sig"}],
        }
        ref = _file_info_to_ref(fi)
        assert ref is not None
        assert ref.url == "https://x.example/sig"
        assert ref.filename == "x.nbf"

    def test_returns_empty_when_url_missing(self) -> None:
        fi = {"name": "orphan.nbf", "locations": {}}
        assert _file_info_to_ref(fi) is None

    def test_legacy_flat_files_list(self) -> None:
        doc = {
            "files": [
                {"filename": "a.png", "url": "https://img.example/a.png", "mimeType": "image/png"},
            ],
        }
        refs = _file_refs(doc)
        assert len(refs) == 1
        assert refs[0].content_type == "image/png"


class TestTimeseriesShape:
    def test_single_channel_shape_has_v1_keys(self) -> None:
        samples = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
        out = _ts_shape_single_channel(samples, sample_rate=1000.0, fmt="vhsb")
        # v1 frontend reads exactly these keys.
        assert set(out.keys()) >= {
            "channels", "timestamps", "sample_count", "format", "error",
        }
        assert isinstance(out["channels"], dict)
        assert set(out["channels"].keys()) == {"ch0"}
        assert out["channels"]["ch0"] == [0.0, 1.0, 2.0, 3.0]
        assert out["sample_count"] == 4
        assert out["format"] == "vhsb"
        assert out["error"] is None

    def test_timestamps_are_seconds_from_zero(self) -> None:
        samples = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        out = _ts_shape_single_channel(samples, sample_rate=2.0, fmt="nbf")
        assert out["timestamps"] == [0.0, 0.5, 1.0]

    def test_timestamps_none_when_sample_rate_zero(self) -> None:
        samples = np.array([1.0, 2.0], dtype=np.float32)
        out = _ts_shape_single_channel(samples, sample_rate=0.0, fmt="nbf")
        assert out["timestamps"] is None

    def test_nan_samples_become_none_for_uplot_gaps(self) -> None:
        samples = np.array([1.0, float("nan"), 3.0], dtype=np.float32)
        got = _to_nullable_list(samples)
        assert got == [1.0, None, 3.0]

    def test_timeseries_error_preserves_v1_shape_keys(self) -> None:
        out = _timeseries_error("no_file", "No timeseries file.")
        assert out["channels"] == {}
        assert out["timestamps"] is None
        assert out["sample_count"] == 0
        assert out["format"] == ""
        assert out["error"] == "No timeseries file."
        assert out["errorKind"] == "no_file"


class TestVhsbParse:
    def test_roundtrip_single_channel(self) -> None:
        # VHSB header is 24 bytes total: 4B magic + 4B version + 8B sample_rate
        # (f64) + 4B n_samples + 4B reserved, then the f32 body.
        magic = b"VHSB"
        version = struct.pack("<i", 1)
        sample_rate = struct.pack("<d", 1000.0)
        samples = np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)
        n_samples = struct.pack("<i", len(samples))
        reserved = b"\x00" * 4
        payload = magic + version + sample_rate + n_samples + reserved + samples.tobytes()
        out = _parse_vhsb(payload)
        assert out["format"] == "vhsb"
        assert out["sample_count"] == 5
        assert len(out["channels"]["ch0"]) == 5
        assert out["timestamps"] and len(out["timestamps"]) == 5

    def test_clamps_sample_count_to_payload_size(self) -> None:
        """Guards against header lies — header says 1M samples but body has 3."""
        magic = b"VHSB"
        version = struct.pack("<i", 1)
        sample_rate = struct.pack("<d", 1000.0)
        n_samples = struct.pack("<i", 1_000_000)  # lie
        reserved = b"\x00" * 4
        samples = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        payload = magic + version + sample_rate + n_samples + reserved + samples.tobytes()
        out = _parse_vhsb(payload)
        # Clamped to 3 actual samples — does not read past end.
        assert out["sample_count"] == 3


class TestNbfParse:
    def test_nbf1_single_channel(self) -> None:
        magic = b"NBF1"
        sample_rate = struct.pack("<f", 100.0)
        channels = struct.pack("<i", 1)
        n = 4
        n_samples = struct.pack("<i", n)
        reserved = b"\x00" * 16
        samples = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        payload = magic + sample_rate + channels + n_samples + reserved + samples.tobytes()
        out = _parse_nbf(payload)
        assert out["format"] == "nbf"
        assert out["channels"]["ch0"] == [1.0, 2.0, 3.0, 4.0]
        assert out["sample_count"] == 4

    def test_nbf1_multi_channel(self) -> None:
        magic = b"NBF1"
        sample_rate = struct.pack("<f", 10.0)
        channels = struct.pack("<i", 2)
        n = 3
        n_samples = struct.pack("<i", n)
        reserved = b"\x00" * 16
        # Channel-interleaved.
        interleaved = np.array(
            [1.0, 10.0, 2.0, 20.0, 3.0, 30.0], dtype=np.float32,
        )
        payload = magic + sample_rate + channels + n_samples + reserved + interleaved.tobytes()
        out = _parse_nbf(payload)
        assert set(out["channels"].keys()) == {"ch0", "ch1"}
        assert out["channels"]["ch0"] == [1.0, 2.0, 3.0]
        assert out["channels"]["ch1"] == [10.0, 20.0, 30.0]
        assert out["sample_count"] == 3


class TestOomGuards:
    """Guards against malicious/corrupt binary payloads claiming billions of
    samples — would otherwise OOM the worker before numpy even tries to read.
    """

    def test_parse_nbf_rejects_oversized_header(self) -> None:
        """Malicious NBF header declaring channels=1000, n_samples=1B → reject."""
        magic = b"NBF1"
        sample_rate = struct.pack("<f", 100.0)
        channels = struct.pack("<i", 1000)
        n_samples = struct.pack("<i", 1_000_000_000)
        reserved = b"\x00" * 16
        # Body doesn't matter — guard fires before np.frombuffer.
        payload = magic + sample_rate + channels + n_samples + reserved + b"\x00" * 32
        with pytest.raises(ValidationFailed):
            _parse_nbf(payload)

    def test_parse_vhsb_rejects_oversized_header(self) -> None:
        """Malicious VHSB header declaring n_samples=1B → reject before allocation."""
        magic = b"VHSB"
        version = struct.pack("<i", 1)
        sample_rate = struct.pack("<d", 1000.0)
        n_samples = struct.pack("<i", 1_000_000_000)
        reserved = b"\x00" * 4
        payload = magic + version + sample_rate + n_samples + reserved + b"\x00" * 32
        with pytest.raises(ValidationFailed):
            _parse_vhsb(payload)

    def test_normal_sized_nbf_parses_without_error(self) -> None:
        """Sanity: legitimate small NBF files still parse after guard was added."""
        magic = b"NBF1"
        sample_rate = struct.pack("<f", 100.0)
        channels = struct.pack("<i", 1)
        n_samples = struct.pack("<i", 4)
        reserved = b"\x00" * 16
        samples = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        payload = magic + sample_rate + channels + n_samples + reserved + samples.tobytes()
        out = _parse_nbf(payload)
        assert out["sample_count"] == 4
        assert out["channels"]["ch0"] == [1.0, 2.0, 3.0, 4.0]

    def test_evaluate_fitcurve_clamps_large_n_samples(self) -> None:
        """fitcurve with n_samples=10M produces MAX_FITCURVE_SAMPLES-length array, no raise."""
        svc = BinaryService(cloud=None)  # type: ignore[arg-type]
        document = {
            "data": {
                "fitcurve": {
                    "functional_form": "linear",
                    "parameters": [1.0, 0.0],
                    "x_min": 0.0,
                    "x_max": 1.0,
                    "n_samples": 10_000_000,
                },
            },
        }
        out = svc.evaluate_fitcurve(document)
        assert len(out["x"]) == MAX_FITCURVE_SAMPLES
        assert len(out["y"]) == MAX_FITCURVE_SAMPLES
