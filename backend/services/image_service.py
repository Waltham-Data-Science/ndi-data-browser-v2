"""image_service — fetch + decode 2D image arrays from NDI binary documents.

Used by the chat's ``fetch_image`` tool to render microscopy / fluorescence /
patch-encounter maps inline as Plotly heatmaps. The PI workflow is:

    "show me the patch encounter map for the Haley accept-reject dataset"
    "show me the cell image from this Bhar memory recording"

Returns a 2D array of floats (one row = one image row), plus min/max for
colorscale anchoring and a source provenance block for citation.

Why a separate service (not a method on BinaryService)?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``BinaryService.get_image`` already exists but returns a base64-encoded
PNG/JPEG datauri for the Document Explorer's image viewer. The chat
needs the actual pixel array so Plotly can render it as a heatmap with
its own colorscale, tooltips, and axis-scaling — a datauri is opaque to
Plotly. Keeping the two paths separate avoids cross-coupling: the
viewer endpoint can keep its base64 contract; the chat endpoint gets
a clean float-array shape.

NDI-native raw image formats (``.nim`` and friends) are NOT yet handled
here — Pillow handles TIFF/PNG/JPEG/GIF which covers the demo datasets.
A future enhancement will route raw-uint8 imageStack files through the
existing ``imageStack_parameters`` sidecar pattern (same shape
``BinaryService.get_raw`` already supports for the Document Explorer).
For now those return ``errorKind="unsupported"``.

Returned dict shape on success::

    {
      "width": int,
      "height": int,
      "data": [[float, ...], ...],      # height x width
      "min": float,
      "max": float,
      "format": "tiff" | "png" | "jpeg" | ...,
      "downsampled": bool,              # True if thumbnailed to <= 512x512
      "source": {
        "dataset_id": str,
        "document_id": str,
        "doc_class": str | None,
        "doc_name": str | None,
        "filename": str | None,
      },
    }

Soft-error envelope (no raise)::

    {"error": "...", "errorKind": "decode|notfound|unsupported"}
"""
from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

from ..auth.session import SessionData
from ..clients.ndi_cloud import NdiCloudClient
from ..observability.logging import get_logger
from .binary_service import _file_refs

if TYPE_CHECKING:  # pragma: no cover
    from PIL import Image as _PILImage  # noqa: F401

log = get_logger(__name__)

# Downsample threshold — Plotly heatmaps slow noticeably above ~512x512
# (each pixel becomes a hover target). The chat surface is small anyway
# (~600 px wide in a typical message); a 512px thumbnail is sharper than
# the rendered size with room for retina-class displays.
MAX_DIMENSION = 512


class ImageService:
    """Decode 2D image arrays from NDI binary documents for chat rendering.

    Reuses BinaryService's file-ref extraction (handles the three observed
    cloud document shapes) plus the cloud client's SSRF-hardened download
    path. Pillow does the format dispatch — TIFF, PNG, JPEG, GIF all flow
    through ``Image.open`` cleanly.
    """

    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def fetch_image(
        self,
        document: dict[str, Any],
        *,
        frame: int = 0,
        session: SessionData | None = None,
    ) -> dict[str, Any]:
        """Fetch + decode the primary image file on ``document``.

        ``frame`` selects which frame to extract from a multi-frame TIFF /
        animated GIF. Out-of-range frames clamp to (0, n_frames-1) and a
        warning is logged.

        Returns a dict matching the module-docstring shape on success, or
        a ``{"error", "errorKind"}`` envelope on a soft failure. The
        envelope shape matches BinaryService's ``_timeseries_error`` so
        the router can pass it through without re-shaping.
        """
        refs = _file_refs(document)
        if not refs:
            return _image_error(
                "notfound",
                "No image file associated with this document.",
            )

        ref = refs[0]
        if not ref.url:
            return _image_error(
                "notfound",
                "No download URL available for this image file.",
            )

        access_token = session.access_token if session else None
        try:
            payload = await self.cloud.download_file(
                ref.url, access_token=access_token,
            )
        except Exception as e:
            log.warning("image_service.download_failed", error=str(e))
            return _image_error(
                "notfound", f"Failed to download image file: {e}",
            )

        return _decode_image(
            payload,
            frame=frame,
            filename=ref.filename,
            source=_source_block(document, filename=ref.filename),
        )


# ---------------------------------------------------------------------------
# Decode helpers — pure functions, no I/O. Tests exercise these directly
# with fixture bytes so the cloud-download stub stays minimal.
# ---------------------------------------------------------------------------


def _decode_image(  # noqa: PLR0911, PLR0912 (linear per-failure-mode returns are clearer than a single accumulator; the branch count is one return per failure mode plus the success path)
    payload: bytes,
    *,
    frame: int,
    filename: str | None,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Decode a raw image payload to a 2D float array.

    Pillow handles TIFF / PNG / JPEG / GIF auto-detect. Multi-channel
    (RGB / RGBA) images are converted to grayscale via Pillow's ``"L"``
    mode — a heatmap renders a single channel, and Plotly's colorscale
    is a more useful visual than three superimposed channels would be
    for the typical microscopy / patch-encounter use case.

    For raw NDI-native image formats (.nim, .imageStack) Pillow will
    raise — we surface as ``unsupported`` and the caller can prompt the
    user to check back later or open the Document Explorer.
    """
    if not payload:
        return _image_error("notfound", "Image file is empty.")

    try:
        # Lazy-import Pillow — matches BinaryService's pattern. Numpy is
        # imported the same way (only paid when decoding actually runs).
        from PIL import Image
    except ImportError as e:
        log.warning("image_service.pillow_unavailable", error=str(e))
        return _image_error("decode", f"Pillow import failed: {e}")

    try:
        # Pillow's `Image.open()` returns an `ImageFile` subclass; subsequent
        # `convert()` calls return the broader `Image.Image` type. We hold a
        # widened reference here so mypy is happy with the rebind below.
        img: Image.Image = Image.open(io.BytesIO(payload))
    except Exception as e:
        log.warning("image_service.pil_open_failed", error=str(e))
        return _image_error(
            "unsupported",
            f"Image format not recognized by Pillow: {e}. "
            "NDI-native raw image formats (.nim, raw imageStack) are not "
            "yet supported by the chat heatmap renderer.",
        )

    fmt = (img.format or "").lower() or "raw"

    # Frame selection for multi-frame containers (TIFF stacks, animated
    # GIFs). Pillow's `seek` raises on out-of-range; we clamp + log a
    # warning rather than failing so the LLM gets a useful fallback.
    n_frames = getattr(img, "n_frames", 1)
    if frame > 0:
        target = min(frame, n_frames - 1) if n_frames > 1 else 0
        if target != frame:
            log.info(
                "image_service.frame_clamped",
                requested=frame, available=n_frames, used=target,
            )
        try:
            img.seek(target)
        except Exception as e:
            log.warning("image_service.frame_seek_failed", error=str(e))
            return _image_error(
                "decode",
                f"Failed to seek to frame {frame} (image has {n_frames} frame(s)): {e}",
            )

    # Convert to single-channel grayscale BEFORE thumbnailing — Pillow's
    # mode-aware downscale is faster on `L` than on `RGBA`, and we'd
    # discard the chroma anyway for the heatmap output.
    if img.mode not in ("L", "I", "I;16", "F"):
        img = img.convert("L")

    # Downsample to bound the response payload size. A 4K TIFF is 16M
    # cells * ~6 bytes-per-cell JSON = ~100 MB response otherwise; the
    # chat surface absolutely cannot ship that. 512x512 keeps the JSON
    # under ~1.5 MB and renders crisply at the chat's column width.
    downsampled = False
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        downsampled = True

    try:
        import numpy as np
        arr = np.asarray(img, dtype=np.float32)
    except Exception as e:
        log.warning("image_service.numpy_convert_failed", error=str(e))
        return _image_error("decode", f"Failed to convert image to array: {e}")

    if arr.ndim != 2:
        # Defensive — convert("L") above should always give a 2D result,
        # but the `F` and `I;16` modes Pillow surfaces for scientific
        # TIFFs can occasionally come through as something else. Flatten
        # the leading dimensions to 2D so the heatmap still renders.
        if arr.ndim == 3 and arr.shape[2] in (1, 3, 4):
            arr = arr.mean(axis=2)
        else:
            return _image_error(
                "decode",
                f"Unexpected array shape after decode: {arr.shape}. "
                "Expected 2D (height x width).",
            )

    # Min/max for Plotly's `zmin`/`zmax` colorscale anchoring. Computed
    # on the float array (after the optional downscale) so the chart
    # matches what Plotly actually renders. Use safe casts to plain
    # Python floats — np.float32 isn't JSON-serializable in some
    # FastAPI response shapes.
    if arr.size == 0:
        return _image_error("decode", "Image decoded to an empty array.")
    arr_min = float(arr.min())
    arr_max = float(arr.max())

    # 2D list-of-lists for the JSON response. Each row materializes once;
    # `.tolist()` is the cheapest numpy → JSON-able path and Pillow's
    # decode already paid the per-pixel cost so this is at most a copy.
    data: list[list[float]] = arr.tolist()

    return {
        "width": int(arr.shape[1]),
        "height": int(arr.shape[0]),
        "data": data,
        "min": arr_min,
        "max": arr_max,
        "format": fmt,
        "downsampled": downsampled,
        "source": source,
    }


def _image_error(error_kind: str, message: str) -> dict[str, Any]:
    """Soft-error envelope — matches the BinaryService ``errorKind`` shape
    so the router doesn't need to re-translate.

    `errorKind` is one of: "notfound", "decode", "unsupported". The LLM
    is taught to surface these plainly without emitting a chart fence.
    """
    return {"error": message, "errorKind": error_kind}


def _source_block(
    document: dict[str, Any], *, filename: str | None,
) -> dict[str, Any]:
    """Build the citation source block. Mirrors signal_service's shape
    so the chat-side reference builder works uniformly across tools.

    Defensive against partial document shapes: every field can be None
    without crashing the dict assembly.
    """
    base = document.get("base", {}) if isinstance(document, dict) else {}
    doc_class: str | None = None
    if isinstance(document, dict):
        cls = document.get("document_class") or {}
        if isinstance(cls, dict):
            doc_class = cls.get("classname") or cls.get("class_name")
        # Bulk-fetch shape buries the class on top-level `className`.
        doc_class = doc_class or document.get("className")
    doc_name = None
    if isinstance(base, dict):
        doc_name = base.get("name")
    return {
        "dataset_id": document.get("datasetId", "") if isinstance(document, dict) else "",
        "document_id": (
            document.get("id") or document.get("_id") or ""
            if isinstance(document, dict) else ""
        ),
        "doc_class": doc_class,
        "doc_name": doc_name,
        "filename": filename,
    }
