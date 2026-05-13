"""ndi_python_service — thin wrappers over the three NDI-python entry points
we use in Phase A.

Why a separate service? Two reasons:

1. **Centralized lazy imports.** NDI-python (~150 MB resident if everything
   loaded eagerly) is gated behind module-level functions that import on first
   call. The rest of the backend doesn't pay the import cost until something
   actually exercises an NDI path.

2. **Consistent error envelope.** Every call returns either a typed Python
   value (numpy array / dict) on success, or `None` (or a sentinel) on a
   recoverable miss. None of these raise on miss — that's the contract our
   callers in `binary_service` and `ontology_service` rely on so they can
   fall through to their existing inline / external paths.

The three entry points are documented in:
`docs/plans/2026-05-13-ndi-python-integration.md`.

Phase B may layer a real `ndi.dataset.Dataset` here. Phase A intentionally
operates only on byte payloads + ID strings, no Dataset object.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Any, Literal

from ..observability.logging import get_logger

log = get_logger(__name__)

# Lightweight import guard. We don't want the *import* of this module to
# pull in pandas / numpy / etc. — that's deferred to first call. The flag
# below caches the result of the first import attempt so subsequent calls
# pay nothing extra. `None` = not-yet-tried; `True` = imported OK;
# `False` = import failed (NDI stack not available; callers fall back).
_NDI_AVAILABLE: bool | None = None


def is_ndi_available() -> bool:
    """Best-effort check that the NDI-python stack is importable. Caches
    the result so health checks + first-request paths don't pay the import
    cost more than once."""
    global _NDI_AVAILABLE  # noqa: PLW0603 — module-level cache flag
    if _NDI_AVAILABLE is not None:
        return _NDI_AVAILABLE
    try:
        # We probe one module from each git-sourced package to make sure
        # the full transitive surface is on PYTHONPATH. Errors here at
        # boot time become clear startup failures rather than mysterious
        # first-request 500s.
        import ndi.ontology  # noqa: F401
        import ndicompress  # noqa: F401
        import vlt.file.custom_file_formats  # noqa: F401
        _NDI_AVAILABLE = True
    except ImportError as e:
        log.warning("ndi_python_service.import_failed", error=str(e))
        _NDI_AVAILABLE = False
    return _NDI_AVAILABLE


# ---------------------------------------------------------------------------
# VHSB — vlt.file.custom_file_formats.vhsb_read
# ---------------------------------------------------------------------------
#
# Important contract from the Phase A recon (see plan doc):
#   - vhsb_read takes a FILE PATH (str), not bytes / BytesIO
#   - It internally reopens the file with `open(filename, 'rb')`
#   - There is ONLY ONE VHSB format. It always begins with a 200-byte
#     ASCII tag ("This is a VHSB file, http://github.com/VH-Lab\n" zero-
#     padded) followed by a 1836-byte binary header, then payload.
#   - Returns `(y, x)` — numpy arrays of values and time-axis samples.
#
# We materialize the payload bytes to a NamedTemporaryFile, call
# vhsb_read, then unlink. The 200-byte text tag is what current
# binary_service.py treats as the `vlt_library` early-return — the
# whole point of Phase A is to actually decode it instead.


def read_vhsb_from_bytes(
    payload: bytes,
) -> dict[str, Any] | None:
    """Decode a VHSB binary payload via vlt.file.

    Returns a dict matching `binary_service._ts_shape_single_channel`'s
    envelope on success (so callers can drop it directly into a
    timeseries response), or `None` on failure so the caller can fall
    back to inline parsing or surface a typed error.

    No raise; all failures log + return None.
    """
    if not is_ndi_available():
        return None
    if not payload or len(payload) < 2036:
        # Minimum: 200 byte text-tag + 1836 byte header. Smaller payloads
        # cannot possibly be valid VHSB.
        return None

    tmp_path = None
    try:
        # vhsb_read needs a real on-disk path. Suffix matters: the helper
        # doesn't sniff the file type from extension, but downstream
        # logging is clearer if we keep it.
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".vhsb", prefix="ndb_vhsb_"
        ) as fh:
            fh.write(payload)
            tmp_path = fh.name

        # Lazy-import inside the function so the import cost is paid only
        # on the first VHSB decode (or never, if no one ever hits this).
        import numpy as np
        from vlt.file.custom_file_formats import vhsb_read, vhsb_readheader

        header = vhsb_readheader(tmp_path)
        n_samples = int(header.get("num_samples", 0))
        if n_samples <= 0:
            log.warning("vhsb_read.bad_header", header=header)
            return None

        y, x = vhsb_read(tmp_path, 0, n_samples)
        if y is None or len(y) == 0:
            log.warning("vhsb_read.empty_payload", n_samples=n_samples)
            return None

        # Translate to the existing envelope shape. y is the value array
        # (possibly multi-dim if Y_dim > 1), x is the time axis. We
        # flatten y to a single channel for now — multi-channel VHSB
        # support is a future enhancement (binary_service's envelope
        # naturally supports it, but the demo datasets are all 1-D).
        sample_rate = float(header.get("X_increment", 0.0))
        # X_increment is seconds-per-sample. Convert to Hz, guarding
        # against zero.
        sample_rate_hz = (1.0 / sample_rate) if sample_rate > 0 else 0.0

        # Flatten to 1-D if vhsb_read returned (N, 1) or (N,).
        values = np.asarray(y).reshape(-1).astype(np.float32, copy=False)

        return {
            "channels": {"ch0": _nan_to_none(values.tolist())},
            "timestamps": np.asarray(x).reshape(-1).astype(np.float64, copy=False).tolist(),
            "sample_count": int(values.size),
            "format": "vhsb",
            "sample_rate_hz": sample_rate_hz,
            "error": None,
        }
    except Exception as e:
        # vhsb_read raises on type mismatch / bad sizes; treat all as soft
        # errors so callers can fall back.
        log.warning("vhsb_read.failed", error=str(e), error_type=type(e).__name__)
        return None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink(missing_ok=True)


def _nan_to_none(values: list[float]) -> list[float | None]:
    """Replace NaN with None so the frontend's uPlot sees explicit gaps
    rather than rendering through NaN-poisoned line segments. Matches the
    `_to_nullable_list` convention in binary_service."""
    import math
    out: list[float | None] = []
    for v in values:
        if isinstance(v, float) and math.isnan(v):
            out.append(None)
        else:
            out.append(float(v))
    return out


# ---------------------------------------------------------------------------
# NDI-compressed binaries — ndicompress.expand_*
# ---------------------------------------------------------------------------
#
# Phase A scope: detect + decompress only. Like vhsb_read, ndicompress
# operates on file paths (subprocess-based, wraps platform-specific C
# executables). Magic byte detection:
#   - Outer wrapper is gzipped tar (.nbf.tgz)
#   - One inner file has the extension `.nbh` and starts with the
#     15-byte ASCII string `b"NDIBINARYHEADER"`
#
# encode_method dispatch (per the recon):
#   1  = Ephys (analog input/output) — most common for us
#   2  = Metadata (JSON-like; rarely shown as timeseries)
#   21 = Digital (uint8 0/1 channels)
#   41 = EventMarkText (sparse markers)
#   61 = Time (time-only data; used as derived axis)
#
# We only auto-handle method 1 (Ephys) on the timeseries path; the
# others surface a typed soft-error and fall through to the existing
# code (which already handles raw .nbf).


def is_ndi_compressed(payload: bytes) -> bool:
    """Cheap prefix check for NDI's `.nbf.tgz` wrapper.

    Doesn't validate the inner contents — that's the job of the actual
    expand call. False positives here are fine because the expand path
    will fail gracefully and the caller will fall back to inline parsing.

    A gzipped tar archive begins with two bytes `0x1f 0x8b` (gzip magic).
    We don't fingerprint deeper than that — every gzip stream we'd see
    in this context is going to be either NDI-compressed or something
    legitimately broken; in either case the expand call will tell us.
    """
    return len(payload) >= 2 and payload[0] == 0x1F and payload[1] == 0x8B


def expand_ephys_from_bytes(payload: bytes) -> dict[str, Any] | None:
    """Decode an NDI-compressed Ephys payload (encode_method=1).

    Returns the same envelope shape as `read_vhsb_from_bytes` so it's a
    drop-in replacement in `BinaryService.get_timeseries`. Multi-channel
    ephys becomes `{"ch0": [...], "ch1": [...], ...}`.

    None on miss / wrong codec / errors. No raise.
    """
    if not is_ndi_available():
        return None
    if not is_ndi_compressed(payload):
        return None

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".nbf.tgz", prefix="ndb_ndic_"
        ) as fh:
            fh.write(payload)
            tmp_path = fh.name

        import ndicompress
        import numpy as np

        # ndicompress.expand_ephys returns (np.ndarray[S, C], None).
        arr, _ = ndicompress.expand_ephys(tmp_path)
        if arr is None or arr.size == 0:
            return None

        # Shape: (n_samples, n_channels). We don't have a sample rate
        # from ndicompress's return (yet — the .nbh header has it but
        # the wrapper doesn't surface it). Caller can post-process with
        # the document's metadata if needed.
        n_samples, n_channels = arr.shape if arr.ndim == 2 else (arr.size, 1)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)

        channels: dict[str, list[float | None]] = {}
        for c in range(n_channels):
            channels[f"ch{c}"] = _nan_to_none(arr[:, c].astype(np.float32).tolist())

        return {
            "channels": channels,
            "timestamps": list(range(n_samples)),  # sample-index axis; caller may rescale
            "sample_count": int(n_samples),
            "format": "nbf_compressed",
            "sample_rate_hz": 0.0,  # unknown without sidecar metadata
            "error": None,
        }
    except Exception as e:
        log.warning(
            "ndicompress.expand_ephys.failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        return None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Ontology — ndi.ontology.lookup
# ---------------------------------------------------------------------------
#
# Phase A's ontology contribution: when our existing external-provider
# lookup misses, fall back to NDI's. NDI's `lookup` knows lab-specific
# terms (WBStrain, Cre lines, internal NDIC identifiers) that public
# providers don't.
#
# Critical contract:
#   - Input is a single CURIE string (`"WBStrain:00000001"`, `"CL:0000540"`)
#   - Output is an OntologyResult with truthy-on-hit, falsy-on-miss
#   - Never raises (provider errors swallowed internally)
#   - Has a small module-level FIFO cache (~100 entries)
#   - Most non-NDIC prefixes hit OLS4 (EBI) via `requests.get`, 30s timeout
#
# We re-cache results in our own redis-backed `ontology_cache` so a hit
# survives process restart. NDI's internal cache is per-process only.


_OntologyLookupKind = Literal["hit", "miss"]


def lookup_ontology(curie: str) -> dict[str, Any] | None:
    """Resolve an ontology CURIE via NDI-python's ontology service.

    Returns the OntologyResult's `.to_dict()` on hit, `None` on miss
    (incl. malformed input, unknown prefix, provider error — all silent
    in NDI's implementation, surfaced as None here).

    Callers in `ontology_service.py` should use this as a FALLBACK after
    their existing external-provider lookup misses — NOT as the primary
    path (NDI's lookup hits the same OLS4 endpoints for many ontologies,
    so duplication would double network traffic for hits we'd see anyway).
    """
    if not is_ndi_available():
        return None
    if not curie or ":" not in curie:
        return None

    try:
        from ndi.ontology import lookup
        result = lookup(curie)
        if not result:  # OntologyResult __bool__ returns True only on hit
            return None
        # to_dict() yields {id, name, prefix, definition, synonyms, short_name}.
        # mypy sees `lookup` as `Any` (NDI-python has no stubs), so the cast
        # is needed to keep the function's declared return type honest under
        # strict mode.
        result_dict: dict[str, Any] = dict(result.to_dict())
        return result_dict
    except Exception as e:
        # NDI's lookup is documented to never raise on misses, but defensive:
        log.warning(
            "ndi.ontology.lookup.failed",
            curie=curie,
            error=str(e),
            error_type=type(e).__name__,
        )
        return None
