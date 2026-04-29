"""v1-compatible TimeseriesData shape + file-ref extraction for binary_service.

Plan §M5 backend step 1: the ported v1 TimeseriesChart reads
`{channels: Record<string, (number|null)[]>, timestamps?, sample_count,
format, error?}`. Pin that contract so future backend edits can't quietly
regress to the old `{y, sampleRate, nSamples}` shape.
"""
from __future__ import annotations

import struct
from unittest.mock import AsyncMock

import numpy as np
import pytest

from backend.clients.ndi_cloud import RangeDownloadResult
from backend.errors import BinaryNotFound, ValidationFailed
from backend.services.binary_service import (
    MAX_FITCURVE_SAMPLES,
    BinaryService,
    _file_info_to_ref,
    _file_refs,
    _parse_nbf,
    _parse_vhsb,
    _range_starts_at_zero,
    _timeseries_error,
    _to_nullable_list,
    _ts_shape_single_channel,
)
from backend.services.file_format import (
    DEFAULT_CONTENT_TYPE,
    detect_content_type,
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


class TestGetRaw:
    """`get_raw` is the PIL-bypass passthrough for raw-uint8 imageStack files.

    The endpoint exists because PIL's `Image.open` blows up on headerless
    raw pixel buffers (no PNG/JPEG magic) and surfaces as
    BINARY_DECODE_FAILED. The frontend opts in for known imageStack docs
    and decodes the bytes itself using the partner
    ``imageStack_parameters`` document for shape/dtype.
    """

    async def test_returns_payload_unchanged(self) -> None:
        """Bytes from cloud.download_file flow through verbatim — no PIL,
        no transformation, no header."""
        cloud = AsyncMock()
        # Arbitrary "raw uint8" bytes — exactly what would be on S3 for an
        # imageStack with no PNG/JPEG magic. PIL would choke on this.
        raw_bytes = bytes(range(256)) * 16  # 4096 bytes, uint8 0..255 cycled
        cloud.download_file.return_value = raw_bytes
        svc = BinaryService(cloud=cloud)
        doc = {
            "data": {
                "files": {
                    "file_info": {
                        "name": "stack.bin",
                        "locations": {
                            "location": "https://ndi-data.s3.us-east-1.amazonaws.com/sig",
                        },
                    },
                },
            },
        }
        out = await svc.get_raw(doc, access_token="t-123")
        assert out == raw_bytes
        # And the cloud client got the URL + token straight through.
        cloud.download_file.assert_awaited_once_with(
            "https://ndi-data.s3.us-east-1.amazonaws.com/sig",
            access_token="t-123",
        )

    async def test_no_file_refs_raises_binary_not_found(self) -> None:
        """When the document has no file refs, the service raises
        BinaryNotFound — caller (router) maps to typed 404, not 502."""
        cloud = AsyncMock()
        svc = BinaryService(cloud=cloud)
        # Doc with empty `files` block — common for malformed or partner
        # docs with no upstream binary.
        doc = {"data": {"files": {}}}
        with pytest.raises(BinaryNotFound):
            await svc.get_raw(doc, access_token=None)
        # Defensive: never call the cloud when no ref exists. Saves an
        # SSRF-allowlist round-trip and avoids a misleading log line.
        cloud.download_file.assert_not_called()

    async def test_propagates_cloud_download_errors(self) -> None:
        """`cloud.download_file` raises BinaryNotFound on 404 and
        CloudInternalError on 5xx. Both propagate untouched — the router's
        exception handler maps them to typed responses."""
        cloud = AsyncMock()
        cloud.download_file.side_effect = BinaryNotFound()
        svc = BinaryService(cloud=cloud)
        doc = {
            "data": {
                "files": {
                    "file_info": {
                        "name": "stack.bin",
                        "locations": {"location": "https://ndi-data.s3.us-east-1.amazonaws.com/x"},
                    },
                },
            },
        }
        with pytest.raises(BinaryNotFound):
            await svc.get_raw(doc, access_token=None)


# ---------------------------------------------------------------------------
# Magic-byte content-type detection (file_format.detect_content_type)
# ---------------------------------------------------------------------------
#
# The /data/raw endpoint sniffs the first ~12 bytes of the payload against
# this table to pick a Content-Type. Pinning the table here so any future
# edit that drops a signature (or changes a MIME) lights up CI.

class TestDetectContentType:
    def test_mp4_ftyp_at_offset_4(self) -> None:
        # 4-byte big-endian box size + ASCII ``ftyp`` + brand. Brand bytes
        # are arbitrary (file-dependent) — the sniff only looks at offsets
        # 4-7. Pick a realistic ``isom`` brand to mirror real MP4 output.
        head = b"\x00\x00\x00\x18ftypisom"
        assert detect_content_type(head) == "video/mp4"

    def test_mp4_with_different_box_size_still_detects(self) -> None:
        # Different size prefix — should still match because we ignore
        # bytes 0-3.
        head = b"\x00\x00\x00\x20ftypmp42extras"
        assert detect_content_type(head) == "video/mp4"

    def test_png_signature(self) -> None:
        head = b"\x89PNG\r\n\x1a\nrest"
        assert detect_content_type(head) == "image/png"

    def test_jpeg_signature(self) -> None:
        head = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        assert detect_content_type(head) == "image/jpeg"

    def test_tiff_little_endian(self) -> None:
        head = b"II*\x00\x08\x00\x00\x00rest"
        assert detect_content_type(head) == "image/tiff"

    def test_tiff_big_endian(self) -> None:
        head = b"MM\x00*\x00\x00\x00\x08rest"
        assert detect_content_type(head) == "image/tiff"

    def test_unknown_falls_back_to_octet_stream(self) -> None:
        # Plausible raw-uint8 imageStack head — pixel bytes, no magic. This
        # is the SHIPPED behavior for the imageStack path that motivated
        # /data/raw in the first place; sniff must not pretend it's
        # video/mp4 just because random bytes happen to land at offset 4.
        from backend.services.file_format import MAGIC_PROBE_BYTES
        head = bytes(range(MAGIC_PROBE_BYTES))
        assert detect_content_type(head) == DEFAULT_CONTENT_TYPE

    def test_empty_bytes_falls_back(self) -> None:
        assert detect_content_type(b"") == DEFAULT_CONTENT_TYPE

    def test_short_bytes_below_mp4_threshold(self) -> None:
        # Only 4 bytes — not enough to test ftyp at offset 4. PNG and JPEG
        # fit, MP4 doesn't. Falls back to octet-stream.
        assert detect_content_type(b"\x00\x00\x00\x18") == DEFAULT_CONTENT_TYPE


# ---------------------------------------------------------------------------
# Range header detection in BinaryService (_range_starts_at_zero)
# ---------------------------------------------------------------------------
#
# Used to decide whether the bytes already returned by the upstream cover
# the file head (so we can magic-byte sniff in place) or whether we have
# to issue a small head-fetch first. Conservatively false on anything we
# can't unambiguously parse as starting at byte 0.

class TestRangeStartsAtZero:
    def test_well_formed_seek_from_start(self) -> None:
        assert _range_starts_at_zero("bytes=0-99") is True

    def test_well_formed_open_ended_from_start(self) -> None:
        assert _range_starts_at_zero("bytes=0-") is True

    def test_seek_into_middle(self) -> None:
        assert _range_starts_at_zero("bytes=100-200") is False

    def test_suffix_range(self) -> None:
        # ``bytes=-100`` means "last 100 bytes" — definitely not starting
        # at 0.
        assert _range_starts_at_zero("bytes=-100") is False

    def test_multi_range_treated_as_not_starting_at_zero(self) -> None:
        # We don't decode multipart/byteranges; treat as "head fetch needed"
        # so the sniff is correct.
        assert _range_starts_at_zero("bytes=0-9, 100-200") is False

    def test_case_insensitive_unit(self) -> None:
        assert _range_starts_at_zero("BYTES=0-9") is True

    def test_malformed(self) -> None:
        assert _range_starts_at_zero("garbage") is False
        assert _range_starts_at_zero("bytes=") is False
        assert _range_starts_at_zero("bytes=abc-") is False


# ---------------------------------------------------------------------------
# BinaryService.get_raw_response — Range pass-through + Content-Type sniff
# ---------------------------------------------------------------------------
#
# Range support is the seek-enabling feature for HTML5 <video> playback of
# MP4-encoded imageStack movies. Tests cover:
#
#   - happy-path full fetch (no Range) → 200 + sniffed MIME + total_size
#   - happy-path Range fetch → 206 + Content-Range surfaced + slice content
#   - mid-file Range with no MP4 magic in slice → head-fetch path runs
#   - Range upstream 416 → ValidationFailed (typed 400)

_S3_URL = "https://ndi-data.s3.us-east-1.amazonaws.com/imagestack/sig"
_DOC_WITH_FILE = {
    "data": {
        "files": {
            "file_info": {
                "name": "stack.mp4",
                "locations": {"location": _S3_URL},
            },
        },
    },
}


class TestGetRawResponse:
    async def test_full_fetch_returns_200_with_sniffed_content_type(self) -> None:
        """No Range header → upstream 200 → router sees Content-Type sniffed
        from the head (MP4 in this case) and total_size from Content-Length."""
        cloud = AsyncMock()
        full_payload = b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 200)
        cloud.download_file_range.return_value = RangeDownloadResult(
            content=full_payload,
            status_code=200,
            content_range=None,
            total_size=len(full_payload),
        )
        svc = BinaryService(cloud=cloud)
        out = await svc.get_raw_response(_DOC_WITH_FILE, access_token=None)
        assert out.status_code == 200
        assert out.content == full_payload
        assert out.content_type == "video/mp4"
        assert out.content_range is None
        assert out.total_size == len(full_payload)
        # Single upstream call (the sniff piggybacks on the bytes in hand).
        assert cloud.download_file_range.await_count == 1
        cloud.download_file_range.assert_awaited_with(
            _S3_URL, access_token=None, range_header=None,
        )

    async def test_range_starting_at_zero_uses_inplace_sniff(self) -> None:
        """``bytes=0-99`` → upstream 206 with the head IN the slice → sniff
        works without an extra round trip."""
        cloud = AsyncMock()
        slice_payload = b"\x00\x00\x00\x18ftypisom" + b"X" * 92  # 100 bytes total
        cloud.download_file_range.return_value = RangeDownloadResult(
            content=slice_payload,
            status_code=206,
            content_range=f"bytes 0-99/{50_000}",
            total_size=50_000,
        )
        svc = BinaryService(cloud=cloud)
        out = await svc.get_raw_response(
            _DOC_WITH_FILE, access_token=None, range_header="bytes=0-99",
        )
        assert out.status_code == 206
        assert out.content == slice_payload
        assert out.content_type == "video/mp4"
        assert out.content_range == "bytes 0-99/50000"
        assert out.total_size == 50_000
        # Still one upstream call — head was inside the slice.
        assert cloud.download_file_range.await_count == 1

    async def test_mid_file_range_triggers_head_fetch_for_sniff(self) -> None:
        """``bytes=10000-20000`` → slice doesn't contain the file head, so
        the service issues a small ``bytes=0-11`` fetch to sniff the magic
        bytes. Total upstream calls: 2 (the slice + the head)."""
        cloud = AsyncMock()
        slice_payload = b"X" * 1024
        head_payload = b"\x00\x00\x00\x18ftyp"  # 8 bytes — enough for MP4 sniff
        # First call (the actual range request) returns the slice; second
        # call (the head fetch for sniffing) returns the file head.
        cloud.download_file_range.side_effect = [
            RangeDownloadResult(
                content=slice_payload,
                status_code=206,
                content_range=f"bytes 10000-11023/{50_000}",
                total_size=50_000,
            ),
            RangeDownloadResult(
                content=head_payload,
                status_code=206,
                content_range=f"bytes 0-11/{50_000}",
                total_size=50_000,
            ),
        ]
        svc = BinaryService(cloud=cloud)
        out = await svc.get_raw_response(
            _DOC_WITH_FILE,
            access_token=None,
            range_header="bytes=10000-11023",
        )
        assert out.status_code == 206
        assert out.content == slice_payload
        # Sniff used the head fetch, not the slice.
        assert out.content_type == "video/mp4"
        assert cloud.download_file_range.await_count == 2

    async def test_416_propagates_as_validation_failed(self) -> None:
        """Upstream 416 (Requested Range Not Satisfiable) bubbles up from
        the cloud client as ValidationFailed → router returns typed 400."""
        cloud = AsyncMock()
        cloud.download_file_range.side_effect = ValidationFailed(
            "Requested byte range is not satisfiable.",
            details={"upstream_status": 416},
        )
        svc = BinaryService(cloud=cloud)
        with pytest.raises(ValidationFailed):
            await svc.get_raw_response(
                _DOC_WITH_FILE,
                access_token=None,
                range_header="bytes=999999999-",
            )

    async def test_no_file_refs_raises_binary_not_found(self) -> None:
        """Same contract as legacy get_raw — empty file refs raise
        BinaryNotFound BEFORE any upstream call."""
        cloud = AsyncMock()
        svc = BinaryService(cloud=cloud)
        with pytest.raises(BinaryNotFound):
            await svc.get_raw_response(
                {"data": {"files": {}}}, access_token=None, range_header=None,
            )
        cloud.download_file_range.assert_not_called()

    async def test_unknown_magic_falls_back_to_octet_stream(self) -> None:
        """Headerless raw-uint8 imageStack bytes (the original motivating use
        case for /data/raw) MUST still surface as application/octet-stream so
        the browser doesn't try to play it as video."""
        cloud = AsyncMock()
        full_payload = bytes(range(256)) * 4  # no magic — random uint8 bytes
        cloud.download_file_range.return_value = RangeDownloadResult(
            content=full_payload,
            status_code=200,
            content_range=None,
            total_size=len(full_payload),
        )
        svc = BinaryService(cloud=cloud)
        out = await svc.get_raw_response(_DOC_WITH_FILE, access_token=None)
        assert out.content_type == "application/octet-stream"

    async def test_head_fetch_failure_degrades_to_octet_stream(self) -> None:
        """If the magic-byte head fetch fails (network blip), we don't 502
        the whole video — fall back to application/octet-stream and let the
        browser decide. Best-effort sniff."""
        cloud = AsyncMock()
        slice_payload = b"X" * 1024
        cloud.download_file_range.side_effect = [
            RangeDownloadResult(
                content=slice_payload,
                status_code=206,
                content_range=f"bytes 10000-11023/{50_000}",
                total_size=50_000,
            ),
            BinaryNotFound(),  # head fetch fails
        ]
        svc = BinaryService(cloud=cloud)
        out = await svc.get_raw_response(
            _DOC_WITH_FILE,
            access_token=None,
            range_header="bytes=10000-11023",
        )
        # The actual range request still succeeded — only the type sniff
        # was best-effort. Content flows through, type defaults.
        assert out.status_code == 206
        assert out.content == slice_payload
        assert out.content_type == "application/octet-stream"
