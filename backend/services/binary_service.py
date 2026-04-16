"""Binary data decoding: NBF (NDI Binary Format), VHSB (VH Lab), image, video, fitcurve.

Ported from v1 with cleanup. Fetches signed file URLs via the cloud document
payload, downloads the bytes through our cloud client, and returns decoded
representations suitable for the frontend's uPlot / image / video components.
"""
from __future__ import annotations

import base64
import io
import struct
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from PIL import Image

from ..clients.ndi_cloud import NdiCloudClient
from ..errors import BinaryDecodeFailed, BinaryNotFound
from ..observability.logging import get_logger

log = get_logger(__name__)

BinaryKind = Literal["timeseries", "image", "video", "fitcurve", "unknown"]


@dataclass(slots=True)
class FileRef:
    url: str
    content_type: str | None
    filename: str | None


class BinaryService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    def detect_kind(self, document: dict[str, Any]) -> BinaryKind:
        """Inspect document for clues. Priority: explicit class, then file extension."""
        class_name = _class_name(document)
        if class_name in ("ndi_document_fitcurve", "fitcurve"):
            return "fitcurve"
        file_refs = _file_refs(document)
        if not file_refs:
            return "unknown"
        first = file_refs[0]
        name = (first.filename or "").lower()
        ct = (first.content_type or "").lower()
        if any(name.endswith(ext) for ext in (".nbf", ".vhsb", ".bin")) or "octet-stream" in ct:
            return "timeseries"
        if any(name.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif")) or ct.startswith("image/"):
            return "image"
        if any(name.endswith(ext) for ext in (".mp4", ".webm", ".mov")) or ct.startswith("video/"):
            return "video"
        return "unknown"

    async def get_timeseries(
        self, document: dict[str, Any], *, access_token: str | None,
    ) -> dict[str, Any]:
        refs = _file_refs(document)
        if not refs:
            raise BinaryNotFound()
        payload = await self.cloud.download_file(refs[0].url, access_token=access_token)
        try:
            name = (refs[0].filename or "").lower()
            if name.endswith(".vhsb") or name.startswith("vh"):
                return _parse_vhsb(payload)
            return _parse_nbf(payload)
        except Exception as e:
            log.warning("binary.decode_failed", kind="timeseries", error=str(e))
            raise BinaryDecodeFailed() from e

    async def get_image(
        self, document: dict[str, Any], *, access_token: str | None,
    ) -> dict[str, Any]:
        refs = _file_refs(document)
        if not refs:
            raise BinaryNotFound()
        payload = await self.cloud.download_file(refs[0].url, access_token=access_token)
        try:
            img = Image.open(io.BytesIO(payload))
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
            n = int(fc.get("n_samples", 200))
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


# --- File reference extraction ---

def _file_refs(document: dict[str, Any]) -> list[FileRef]:
    out: list[FileRef] = []
    files = (document.get("files") or {}).get("file_info") or document.get("files") or []
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            url = f.get("signedUrl") or f.get("url")
            if not isinstance(url, str):
                locs = f.get("locations") or []
                if isinstance(locs, list) and locs:
                    url = locs[0].get("signedUrl") or locs[0].get("url")
            if isinstance(url, str):
                out.append(FileRef(
                    url=url,
                    content_type=f.get("contentType") or f.get("mimeType"),
                    filename=f.get("filename") or f.get("name"),
                ))
    return out


def _class_name(document: dict[str, Any]) -> str:
    return (
        document.get("className")
        or document.get("class_name")
        or ((document.get("data") or {}).get("document_class") or {}).get("class_name")
        or ""
    )


# --- NBF (NDI Binary Format) parser ---
#
# NBF is a simple structured binary with a 32-byte ASCII header followed by
# a body of samples. We support the common case: version 1, float32 samples,
# single-channel, with sample_rate stored in the header.
#
# The full spec lives in DID-python. What we implement here is compatible with
# the tutorial-shipped datasets' `session.reference` timeseries.

def _parse_nbf(data: bytes) -> dict[str, Any]:
    if len(data) < 32:
        raise ValueError("NBF payload too small")
    magic = data[:4]
    if magic != b"NBF1":
        # Fall back to "assume float32 array" for legacy.
        samples = np.frombuffer(data, dtype=np.float32)
        return {"y": samples.tolist(), "sampleRate": 1.0, "unit": ""}
    # Offsets inferred from header layout: little-endian 32-bit ints + f32s.
    sample_rate = struct.unpack_from("<f", data, 4)[0]
    channels = struct.unpack_from("<i", data, 8)[0]
    n_samples = struct.unpack_from("<i", data, 12)[0]
    offset = 32
    count = max(1, channels) * max(1, n_samples)
    samples = np.frombuffer(data, dtype=np.float32, count=count, offset=offset)
    if channels > 1:
        samples = samples.reshape(n_samples, channels)
    return {
        "y": samples.tolist(),
        "channels": channels,
        "sampleRate": sample_rate,
        "nSamples": n_samples,
    }


def _parse_vhsb(data: bytes) -> dict[str, Any]:
    """Minimal VHSB parser: header (4B 'VHSB' + version + sampleRate + nSamples) then float32."""
    if len(data) < 24 or data[:4] != b"VHSB":
        raise ValueError("Not a VHSB file")
    sample_rate = struct.unpack_from("<d", data, 8)[0]
    n_samples = struct.unpack_from("<i", data, 16)[0]
    samples = np.frombuffer(data, dtype=np.float32, count=n_samples, offset=24)
    return {"y": samples.tolist(), "sampleRate": sample_rate, "nSamples": n_samples}


def _evaluate_form(form: str, params: list[float], xs: np.ndarray) -> np.ndarray:
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
