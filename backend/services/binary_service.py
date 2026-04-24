"""Binary data decoding: NBF (NDI Binary Format), VHSB (VH Lab), image, video, fitcurve.

Fetches signed file URLs via the cloud document payload, downloads the
bytes through our cloud client, and returns decoded representations
suitable for the frontend's uPlot / image / video components.

TimeseriesData shape (v1-compatible per plan §M5 backend step 1):

    {
      "channels": {<name>: (number|null)[]},
      "timestamps": number[] | None,
      "sample_count": int,
      "format": "nbf" | "vhsb",
      "error": str | None,
    }

The v1 frontend expects this shape exactly; `TimeseriesChart.tsx` does
`Object.keys(data.channels)` and detects `ai`/`ao` named channels for
sweep coloring. Single-channel NBF/VHSB maps to `{ch0: [...]}`.
"""
from __future__ import annotations

import base64
import io
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ..clients.ndi_cloud import NdiCloudClient
from ..errors import BinaryDecodeFailed, BinaryNotFound, ValidationFailed
from ..observability.logging import get_logger

# numpy + PIL are only needed when a request actually decodes image or
# timeseries binary — they're imported lazily inside the relevant
# functions below. Eagerly loading them at module import time (and
# therefore at worker boot via routers/binary.py -> app.py) cost ~500ms
# per worker x 4 workers = ~2s extra cold start per Railway deploy.
# Audit 2026-04-23, issue #57. `from __future__ import annotations`
# above makes signature annotations lazy strings so we don't need the
# TYPE_CHECKING dance for them.
if TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    from PIL import Image  # noqa: F401

log = get_logger(__name__)

BinaryKind = Literal["timeseries", "image", "video", "fitcurve", "unknown"]

# Sanity caps for binary payload decoding — guard against malicious or corrupt
# files declaring billions of samples. Gemini Shard C HIGH, 2026-04-17.
MAX_NBF_SAMPLES = 100_000_000
MAX_VHSB_SAMPLES = 100_000_000  # use the same value unless VHSB's shape argues for different
MAX_FITCURVE_SAMPLES = 10_000


@dataclass(slots=True)
class FileRef:
    url: str
    content_type: str | None
    filename: str | None


class BinaryService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    def detect_kind(self, document: dict[str, Any]) -> BinaryKind:
        """Inspect document for clues. Priority: explicit class → file
        extension → content type.

        Class-name heuristics cover classes that don't carry a file extension
        (e.g. Haley's `imageStack` has no `.png` suffix on its file_info.name).
        """
        class_name = _class_name(document)
        by_class = _kind_from_class_name(class_name)
        if by_class is not None:
            return by_class
        file_refs = _file_refs(document)
        if not file_refs:
            return "unknown"
        first = file_refs[0]
        return _kind_from_file_meta(first.filename, first.content_type)

    async def get_timeseries(  # noqa: PLR0911
        self, document: dict[str, Any], *, access_token: str | None,
    ) -> dict[str, Any]:
        """Return v1-compatible TimeseriesData.

        On soft errors (missing file, failed download, unknown format) we
        populate `error` instead of raising so the frontend can surface a
        friendly message without an error-boundary crash. Hard errors
        (partial decode with irrecoverable data) still raise BinaryDecodeFailed.
        The multi-return shape follows the error/success branches; collapsing
        via a single accumulator would fight the code's narrative.
        """
        refs = _file_refs(document)
        if not refs:
            return _timeseries_error("no_file", "No timeseries file associated with this document.")

        ref = refs[0]
        if not ref.url:
            return _timeseries_error("no_download_url", "No download URL available for this file.")

        try:
            payload = await self.cloud.download_file(ref.url, access_token=access_token)
        except Exception as e:
            log.warning("binary.download_failed", error=str(e))
            return _timeseries_error("download", f"Failed to download file: {e}")

        name = (ref.filename or "").lower()
        # VH-Lab's VHSB files use a text metadata header ("This is a VHSB file,
        # http://github.com/VH-Lab") followed by typed binary slots. The v1
        # decoder used the DID-python `vlt` library for this. v2 doesn't bundle
        # vlt today; we surface the same "vlt library not available" soft error
        # the v1 TimeseriesChart already maps to a friendly message.
        head = payload[:5] if len(payload) >= 5 else b""
        if head.startswith(b"This "):
            return _timeseries_error(
                "vlt_library",
                "vlt library is not available on this server — full VHSB "
                "decoding requires the DID-python `vlt` extension. The raw "
                "file is available in the document's Files section.",
            )
        try:
            if name.endswith(".vhsb") or (payload[:4] == b"VHSB"):
                return _parse_vhsb(payload)
            return _parse_nbf(payload)
        except Exception as e:
            log.warning("binary.decode_failed", kind="timeseries", error=str(e))
            # Soft error instead of 500 — v1 behavior.
            return _timeseries_error(
                "decode",
                f"Could not decode {name or 'this'} binary file. "
                "Format may not be supported.",
            )

    async def get_image(
        self, document: dict[str, Any], *, access_token: str | None,
    ) -> dict[str, Any]:
        refs = _file_refs(document)
        if not refs:
            raise BinaryNotFound()
        payload = await self.cloud.download_file(refs[0].url, access_token=access_token)
        try:
            # Lazy-import PIL (audit #57) — see module docstring.
            from PIL import Image
            img = Image.open(io.BytesIO(payload))
            n_frames = getattr(img, "n_frames", 1)
            buf = io.BytesIO()
            img.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            fmt = "PNG" if img.mode in ("RGBA", "LA", "P") else "JPEG"
            img.save(buf, fmt)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return {
                "dataUri": f"data:image/{fmt.lower()};base64,{b64}",
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "nFrames": n_frames,
            }
        except Exception as e:
            log.warning("binary.decode_failed", kind="image", error=str(e))
            raise BinaryDecodeFailed() from e

    async def get_video_url(self, document: dict[str, Any]) -> dict[str, Any]:
        refs = _file_refs(document)
        if not refs:
            raise BinaryNotFound()
        return {"url": refs[0].url, "contentType": refs[0].content_type or "video/mp4"}

    def evaluate_fitcurve(self, document: dict[str, Any]) -> dict[str, Any]:
        try:
            data = document.get("data", {})
            fc = data.get("fitcurve", data)
            params: list[float] = list(fc.get("parameters", []))
            form = str(fc.get("functional_form", "linear")).lower()
            x_min = float(fc.get("x_min", 0.0))
            x_max = float(fc.get("x_max", 1.0))
            n_samples = int(fc.get("n_samples", 200))
            n = min(n_samples, MAX_FITCURVE_SAMPLES)
            # Lazy-import numpy (audit #57) — see module docstring.
            import numpy as np
            xs = np.linspace(x_min, x_max, n)
            ys = _evaluate_form(form, params, xs)
            return {
                "form": form,
                "parameters": params,
                "x": xs.tolist(),
                "y": ys.tolist(),
            }
        except Exception as e:
            log.warning("binary.decode_failed", kind="fitcurve", error=str(e))
            raise BinaryDecodeFailed() from e


# ---------------------------------------------------------------------------
# detect_kind helpers
# ---------------------------------------------------------------------------

_CLASS_KIND_MAP: dict[str, BinaryKind] = {
    "ndi_document_fitcurve": "fitcurve",
    "fitcurve": "fitcurve",
    "imageStack": "image",
    "image": "image",
    "imageMovie": "image",
    "thumbnail": "image",
    "video": "video",
    "videoClip": "video",
    "element_epoch": "timeseries",
    "epoch": "timeseries",
    "session_reference": "timeseries",
    "session.reference": "timeseries",
}


def _kind_from_class_name(class_name: str) -> BinaryKind | None:
    return _CLASS_KIND_MAP.get(class_name)


def _kind_from_file_meta(filename: str | None, content_type: str | None) -> BinaryKind:
    name = (filename or "").lower()
    ct = (content_type or "").lower()
    if any(name.endswith(ext) for ext in (".nbf", ".vhsb", ".bin")) or "octet-stream" in ct:
        return "timeseries"
    if any(name.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif")) or ct.startswith("image/"):
        return "image"
    if any(name.endswith(ext) for ext in (".mp4", ".webm", ".mov")) or ct.startswith("video/"):
        return "video"
    return "unknown"


# ---------------------------------------------------------------------------
# File reference extraction — handles the 3 observed cloud shapes:
#   1. files.file_info is a single dict with `name` + `locations: {location}`
#      (the common case — element_epoch docs in Haley/VH).
#   2. files.file_info is a list of such dicts (multi-file documents).
#   3. files is itself a list of {url, filename, contentType} (legacy shape).
# ---------------------------------------------------------------------------

def _file_refs(document: dict[str, Any]) -> list[FileRef]:
    files = (document.get("data") or {}).get("files") or document.get("files") or {}
    out: list[FileRef] = []

    # Shape 1 + 2: {file_list: [...], file_info: dict | list}
    file_info = files.get("file_info") if isinstance(files, dict) else None
    if isinstance(file_info, dict):
        ref = _file_info_to_ref(file_info)
        if ref:
            out.append(ref)
    elif isinstance(file_info, list):
        for fi in file_info:
            if not isinstance(fi, dict):
                continue
            ref = _file_info_to_ref(fi)
            if ref:
                out.append(ref)

    # Shape 3: legacy flat list on `files`
    if not out and isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            url = f.get("signedUrl") or f.get("url")
            if isinstance(url, str):
                out.append(FileRef(
                    url=url,
                    content_type=f.get("contentType") or f.get("mimeType"),
                    filename=f.get("filename") or f.get("name"),
                ))

    return out


def _file_info_to_ref(fi: dict[str, Any]) -> FileRef | None:
    """Extract a (url, content_type, filename) triple from a `file_info` entry.
    `locations` can be a single dict or a list of them; URL key is `location`
    (signed URL) or `signedUrl`."""
    name = fi.get("name") or fi.get("filename")
    content_type = fi.get("contentType") or fi.get("mimeType")
    loc = fi.get("locations")
    url: str | None = None
    if isinstance(loc, dict):
        url = loc.get("location") or loc.get("signedUrl") or loc.get("url")
    elif isinstance(loc, list):
        for entry in loc:
            if isinstance(entry, dict):
                u = entry.get("location") or entry.get("signedUrl") or entry.get("url")
                if isinstance(u, str):
                    url = u
                    break
    if not url:
        # Direct URL on file_info.
        url = fi.get("signedUrl") or fi.get("url") or fi.get("location")
    if not isinstance(url, str):
        return None
    return FileRef(url=url, content_type=content_type, filename=name)


def _class_name(document: dict[str, Any]) -> str:
    return (
        document.get("className")
        or document.get("class_name")
        or ((document.get("data") or {}).get("document_class") or {}).get("class_name")
        or ""
    )


# ---------------------------------------------------------------------------
# v1-compatible shape helpers
# ---------------------------------------------------------------------------

def _timeseries_error(kind: str, message: str) -> dict[str, Any]:
    """Return a non-raising error payload the frontend maps to a friendly
    message (cf. v1 TimeseriesChart's error-map branch). `kind` is included
    as a hint; the message is what the chart will display."""
    return {
        "channels": {},
        "timestamps": None,
        "sample_count": 0,
        "format": "",
        "error": message,
        "errorKind": kind,
    }


# ---------------------------------------------------------------------------
# NBF (NDI Binary Format) parser
# ---------------------------------------------------------------------------
#
# NBF magic: 4 bytes "NBF1" + float32 sample_rate + int32 channels + int32
# n_samples + 16 bytes reserved → 32-byte header, then float32 samples
# (channel-interleaved if channels > 1). Legacy files without the magic are
# treated as a raw float32 stream with sampleRate=1.

def _parse_nbf(data: bytes) -> dict[str, Any]:
    # Lazy-import numpy (audit #57) — see module docstring.
    import numpy as np
    if len(data) < 32:
        raise ValueError("NBF payload too small")
    magic = data[:4]
    if magic != b"NBF1":
        # Legacy flat float32 array.
        samples = np.frombuffer(data, dtype=np.float32)
        return _ts_shape_single_channel(samples, sample_rate=1.0, fmt="nbf")

    sample_rate = struct.unpack_from("<f", data, 4)[0]
    channels = max(1, struct.unpack_from("<i", data, 8)[0])
    n_samples = max(1, struct.unpack_from("<i", data, 12)[0])
    if channels * n_samples > MAX_NBF_SAMPLES:
        raise ValidationFailed(
            f"NBF header declares too many samples (channels={channels}, n_samples={n_samples}).",
        )
    count = channels * n_samples
    body = np.frombuffer(data, dtype=np.float32, count=count, offset=32)
    if channels == 1:
        return _ts_shape_single_channel(body, sample_rate=sample_rate, fmt="nbf")
    frames = body.reshape(n_samples, channels)
    ch_dict = {f"ch{i}": _to_nullable_list(frames[:, i]) for i in range(channels)}
    timestamps = _timestamps_for(n_samples, sample_rate)
    return {
        "channels": ch_dict,
        "timestamps": timestamps,
        "sample_count": n_samples,
        "format": "nbf",
        "error": None,
    }


# ---------------------------------------------------------------------------
# VHSB parser — Van Hooser Lab binary.
# ---------------------------------------------------------------------------
#
# Minimal parser: 4 bytes "VHSB" + 4 bytes version + 8 bytes sample_rate
# (float64, big offsets) + 4 bytes n_samples, then float32 body.
#
# Tutorial-shipped VH files use this layout. Full spec lives in DID-python.

def _parse_vhsb(data: bytes) -> dict[str, Any]:
    # Lazy-import numpy (audit #57) — see module docstring.
    import numpy as np
    if len(data) < 24 or data[:4] != b"VHSB":
        raise ValueError("Not a VHSB file")
    sample_rate = struct.unpack_from("<d", data, 8)[0]
    n_samples = struct.unpack_from("<i", data, 16)[0]
    if n_samples > MAX_VHSB_SAMPLES:
        raise ValidationFailed(
            f"VHSB header declares too many samples (n_samples={n_samples}).",
        )
    # Clamp to what the bytes actually contain — guards against header lies.
    max_samples = max(0, (len(data) - 24) // 4)
    n_samples = min(max_samples, max(0, n_samples))
    samples = np.frombuffer(data, dtype=np.float32, count=n_samples, offset=24)
    return _ts_shape_single_channel(samples, sample_rate=sample_rate, fmt="vhsb")


def _ts_shape_single_channel(
    samples: np.ndarray, *, sample_rate: float, fmt: str,
) -> dict[str, Any]:
    n = int(samples.size)
    return {
        "channels": {"ch0": _to_nullable_list(samples)},
        "timestamps": _timestamps_for(n, sample_rate),
        "sample_count": n,
        "format": fmt,
        "error": None,
    }


def _to_nullable_list(arr: np.ndarray) -> list[Any]:
    """Convert a numpy array to a list, replacing NaN with None so the
    frontend's uPlot sees explicit `null` gaps (v1 convention for sweep
    detection)."""
    import math
    out: list[Any] = []
    for v in arr.tolist():
        if isinstance(v, float) and math.isnan(v):
            out.append(None)
        else:
            out.append(float(v))
    return out


def _timestamps_for(n: int, sample_rate: float) -> list[float] | None:
    """Linear time axis in seconds. Returns None when sample_rate is 0/invalid
    (frontend falls back to sample index)."""
    if n <= 0 or sample_rate <= 0:
        return None
    dt = 1.0 / sample_rate
    return [i * dt for i in range(n)]


def _evaluate_form(form: str, params: list[float], xs: np.ndarray) -> np.ndarray:
    # Lazy-import numpy (audit #57) — see module docstring. Caller
    # (``evaluate_fitcurve``) has already imported numpy to build ``xs``,
    # so this is a cache hit, but keeping it local means this helper
    # never triggers import by itself if called on a pre-existing array.
    import numpy as np
    if form == "linear" and len(params) >= 2:
        a, b = params[0], params[1]
        return a * xs + b
    if form == "quadratic" and len(params) >= 3:
        return params[0] * xs**2 + params[1] * xs + params[2]
    if form == "gaussian" and len(params) >= 3:
        amp, mu, sigma = params[0], params[1], max(1e-9, params[2])
        return amp * np.exp(-((xs - mu) ** 2) / (2 * sigma**2))
    if form == "exponential" and len(params) >= 2:
        a, k = params[0], params[1]
        return a * np.exp(k * xs)
    if form == "power" and len(params) >= 2:
        a, k = params[0], params[1]
        safe = np.where(xs == 0, 1e-12, xs)
        return a * np.power(safe, k)
    return np.zeros_like(xs)
