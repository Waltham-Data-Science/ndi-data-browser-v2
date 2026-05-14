"""Unit tests for image_service.

Coverage targets:
  - Happy path: PNG / TIFF / JPEG payloads decode to a 2D float array
    with the right (width, height, min, max, format) envelope.
  - Downsampling: images larger than MAX_DIMENSION get thumbnailed and
    the response sets `downsampled: True`.
  - Multi-channel input: RGB / RGBA images convert to grayscale.
  - Missing document file refs: returns errorKind="notfound".
  - Pillow can't open the bytes: returns errorKind="unsupported"
    (covers raw .nim and other NDI-native formats).
  - Empty payload: returns errorKind="notfound".

The cloud-download path is stubbed via AsyncMock so we only exercise
the decode pipeline. Pure helpers (`_decode_image`, `_source_block`,
`_image_error`) are also exercised directly with fixture bytes.
"""
from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest
from PIL import Image

from backend.services.image_service import (
    MAX_DIMENSION,
    ImageService,
    _decode_image,
    _image_error,
    _source_block,
)

# ---------------------------------------------------------------------------
# Fixture builders — produce raw image bytes Pillow can decode.
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int, height: int, mode: str = "L") -> bytes:
    """Build a PNG payload. Default mode `L` is single-channel grayscale.

    The pixel values are a deterministic gradient so tests can assert
    min/max bracket the expected range.
    """
    img = Image.new(mode, (width, height))
    pixels = img.load()
    assert pixels is not None  # narrow Pillow's PixelAccess|None type
    for y in range(height):
        for x in range(width):
            # 0..255 ramp diagonally so min < max for any non-trivial size.
            value = (x + y) % 256
            if mode == "L":
                pixels[x, y] = value
            else:
                # Multi-channel: write the ramp across all channels so the
                # grayscale conversion is well-defined and predictable.
                pixels[x, y] = tuple([value] * len(mode))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_tiff_bytes(width: int, height: int) -> bytes:
    """Build a single-frame TIFF — TIFF is the common scientific format."""
    img = Image.new("L", (width, height))
    pixels = img.load()
    assert pixels is not None  # narrow Pillow's PixelAccess|None type
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (x + y) % 256
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


def _doc_with_file(url: str, filename: str = "image.png") -> dict[str, Any]:
    """Build a document shape that matches the cloud's file_info envelope."""
    return {
        "id": "doc-abc",
        "datasetId": "ds-xyz",
        "className": "image",
        "data": {
            "files": {
                "file_list": [filename],
                "file_info": {
                    "name": filename,
                    "locations": {"location": url},
                },
            },
            "base": {"name": "Test image"},
            "document_class": {"classname": "image"},
        },
    }


# ---------------------------------------------------------------------------
# _decode_image — pure-function tests over raw payloads
# ---------------------------------------------------------------------------


class TestDecodeImage:
    def test_decodes_png_to_2d_array(self) -> None:
        payload = _make_png_bytes(8, 4)
        result = _decode_image(
            payload, frame=0, filename="image.png",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": "image", "doc_name": "x", "filename": "image.png"},
        )
        assert "error" not in result
        assert result["width"] == 8
        assert result["height"] == 4
        assert result["format"] == "png"
        assert isinstance(result["data"], list)
        assert len(result["data"]) == 4
        assert len(result["data"][0]) == 8
        # min < max because the gradient covers a non-trivial range.
        assert result["min"] < result["max"]
        assert result["downsampled"] is False

    def test_decodes_tiff(self) -> None:
        payload = _make_tiff_bytes(16, 16)
        result = _decode_image(
            payload, frame=0, filename="image.tiff",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": "image", "doc_name": "x", "filename": "image.tiff"},
        )
        assert "error" not in result
        assert result["format"] == "tiff"
        assert result["width"] == 16
        assert result["height"] == 16

    def test_downsamples_when_above_max_dimension(self) -> None:
        # Use a non-square image to verify both dimensions get scaled.
        big_w = MAX_DIMENSION + 200
        big_h = MAX_DIMENSION + 100
        payload = _make_png_bytes(big_w, big_h)
        result = _decode_image(
            payload, frame=0, filename="big.png",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": "image", "doc_name": "x", "filename": "big.png"},
        )
        assert "error" not in result
        assert result["downsampled"] is True
        # Thumbnail preserves aspect ratio; the longer side must be at
        # MAX_DIMENSION and the other proportionally smaller.
        assert max(result["width"], result["height"]) == MAX_DIMENSION
        assert result["width"] <= MAX_DIMENSION
        assert result["height"] <= MAX_DIMENSION

    def test_does_not_downsample_when_within_bounds(self) -> None:
        payload = _make_png_bytes(MAX_DIMENSION, MAX_DIMENSION)
        result = _decode_image(
            payload, frame=0, filename="ok.png",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": "image", "doc_name": "x", "filename": "ok.png"},
        )
        assert result["downsampled"] is False
        assert result["width"] == MAX_DIMENSION
        assert result["height"] == MAX_DIMENSION

    def test_rgb_converts_to_grayscale(self) -> None:
        """A 3-channel RGB image should come back as a single-channel
        2D array (not a 3D RGB array). Plotly heatmaps expect 2D."""
        payload = _make_png_bytes(8, 8, mode="RGB")
        result = _decode_image(
            payload, frame=0, filename="color.png",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": "image", "doc_name": "x", "filename": "color.png"},
        )
        assert "error" not in result
        # 2D — each row is a list of scalars, not a list of triples.
        assert isinstance(result["data"][0][0], float)

    def test_empty_payload_returns_notfound(self) -> None:
        result = _decode_image(
            b"", frame=0, filename="x",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": None, "doc_name": None, "filename": "x"},
        )
        assert result["errorKind"] == "notfound"

    def test_unrecognized_bytes_return_unsupported(self) -> None:
        """Raw NDI .nim payloads (or any non-image bytes) should surface
        as `unsupported` so the LLM can communicate it cleanly."""
        # Random bytes that don't match any image magic Pillow knows.
        payload = b"\x00\x01\x02\x03not a real image\xff\xfe" * 8
        result = _decode_image(
            payload, frame=0, filename="weird.nim",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": None, "doc_name": None, "filename": "weird.nim"},
        )
        assert result["errorKind"] == "unsupported"
        assert "not yet supported" in result["error"] or "not recognized" in result["error"]

    def test_min_max_match_array_extremes(self) -> None:
        """min/max should be the actual array extremes (used as Plotly
        zmin/zmax). Manufactured ramp guarantees min=0, max approaches
        the modulus."""
        payload = _make_png_bytes(16, 16)
        result = _decode_image(
            payload, frame=0, filename="ramp.png",
            source={"dataset_id": "d", "document_id": "doc",
                    "doc_class": None, "doc_name": None, "filename": "ramp.png"},
        )
        # Reconstruct from the response to verify
        arr = np.asarray(result["data"], dtype=np.float32)
        assert result["min"] == float(arr.min())
        assert result["max"] == float(arr.max())


# ---------------------------------------------------------------------------
# _image_error — sanity check the envelope shape
# ---------------------------------------------------------------------------


class TestImageError:
    def test_envelope_shape(self) -> None:
        env = _image_error("decode", "Bad bytes")
        assert env == {"error": "Bad bytes", "errorKind": "decode"}

    def test_all_three_kinds_recognized(self) -> None:
        for kind in ("notfound", "decode", "unsupported"):
            env = _image_error(kind, "msg")
            assert env["errorKind"] == kind


# ---------------------------------------------------------------------------
# _source_block — citation provenance for the chat reference chip
# ---------------------------------------------------------------------------


class TestSourceBlock:
    def test_extracts_document_metadata(self) -> None:
        doc = {
            "id": "doc-abc",
            "datasetId": "ds-xyz",
            "className": "image",
            "data": {
                "base": {"name": "Patch encounter map S1"},
                "document_class": {"classname": "image"},
            },
            "base": {"name": "Patch encounter map S1"},
            "document_class": {"classname": "image"},
        }
        block = _source_block(doc, filename="cell_image.tiff")
        assert block["dataset_id"] == "ds-xyz"
        assert block["document_id"] == "doc-abc"
        assert block["doc_class"] == "image"
        assert block["doc_name"] == "Patch encounter map S1"
        assert block["filename"] == "cell_image.tiff"

    def test_handles_missing_fields(self) -> None:
        """A bare document shouldn't crash _source_block assembly."""
        block = _source_block({}, filename=None)
        assert block["doc_class"] is None
        assert block["doc_name"] is None
        assert block["filename"] is None


# ---------------------------------------------------------------------------
# ImageService — end-to-end with the cloud client stubbed
# ---------------------------------------------------------------------------


class TestImageServiceFetchImage:
    @pytest.mark.asyncio
    async def test_happy_path_png(self) -> None:
        png_bytes = _make_png_bytes(8, 8)
        cloud = AsyncMock()
        cloud.download_file.return_value = png_bytes
        svc = ImageService(cloud)
        doc = _doc_with_file("https://signed.example/image.png", "image.png")
        result = await svc.fetch_image(doc, frame=0, session=None)
        assert "error" not in result
        assert result["width"] == 8
        assert result["height"] == 8
        assert result["format"] == "png"
        cloud.download_file.assert_awaited_once_with(
            "https://signed.example/image.png", access_token=None,
        )

    @pytest.mark.asyncio
    async def test_no_file_refs_returns_notfound(self) -> None:
        """An empty file_info on the document should not reach the cloud."""
        cloud = AsyncMock()
        svc = ImageService(cloud)
        doc = {"id": "d", "datasetId": "ds", "data": {"files": {}}}
        result = await svc.fetch_image(doc, frame=0, session=None)
        assert result["errorKind"] == "notfound"
        cloud.download_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_download_failure_returns_notfound(self) -> None:
        cloud = AsyncMock()
        cloud.download_file.side_effect = RuntimeError("403 from S3")
        svc = ImageService(cloud)
        doc = _doc_with_file("https://signed.example/image.png")
        result = await svc.fetch_image(doc, frame=0, session=None)
        assert result["errorKind"] == "notfound"
        assert "Failed to download" in result["error"]

    @pytest.mark.asyncio
    async def test_unsupported_bytes_return_unsupported(self) -> None:
        """When the document file is downloaded but Pillow can't decode it
        (e.g. raw .nim payload), the service surfaces `unsupported` so the
        LLM can tell the user without trying to render a chart."""
        cloud = AsyncMock()
        cloud.download_file.return_value = b"NOT AN IMAGE" * 32
        svc = ImageService(cloud)
        doc = _doc_with_file("https://signed.example/weird.nim", "weird.nim")
        result = await svc.fetch_image(doc, frame=0, session=None)
        assert result["errorKind"] == "unsupported"

    @pytest.mark.asyncio
    async def test_downsamples_oversized_image(self) -> None:
        big_payload = _make_png_bytes(MAX_DIMENSION + 256, MAX_DIMENSION + 256)
        cloud = AsyncMock()
        cloud.download_file.return_value = big_payload
        svc = ImageService(cloud)
        doc = _doc_with_file("https://signed.example/big.png")
        result = await svc.fetch_image(doc, frame=0, session=None)
        assert result["downsampled"] is True
        assert max(result["width"], result["height"]) == MAX_DIMENSION

    @pytest.mark.asyncio
    async def test_source_block_propagates_filename(self) -> None:
        """The source block returned to the chat should include the
        underlying filename so the LLM can name the file in its answer."""
        cloud = AsyncMock()
        cloud.download_file.return_value = _make_png_bytes(4, 4)
        svc = ImageService(cloud)
        doc = _doc_with_file("https://signed.example/cell_image.tiff", "cell_image.tiff")
        result = await svc.fetch_image(doc, frame=0, session=None)
        assert "error" not in result
        assert result["source"]["filename"] == "cell_image.tiff"
        assert result["source"]["document_id"] == "doc-abc"
        assert result["source"]["dataset_id"] == "ds-xyz"
