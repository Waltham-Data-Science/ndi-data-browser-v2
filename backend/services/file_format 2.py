"""Content-Type detection for raw binary payloads served by ``/data/raw``.

The raw passthrough endpoint (``BinaryService.get_raw``) historically
returned ``application/octet-stream`` for everything, which prevented
the browser's ``<video>`` / ``<img>`` elements from playing or rendering
the bytes natively. This helper sniffs a small leading slice of the
payload against a curated table of magic-byte signatures and returns
the matching MIME type.

Why magic bytes (not file extension)? The endpoint sees raw S3 bytes;
the file extension is metadata on a separate path (``file_info.name``)
and is sometimes missing or wrong. Sniffing the bytes is authoritative
for the formats we care about.

Why a tiny hand-rolled table (not ``python-magic``)? We only need a
handful of formats — MP4 (the motivating use case for HTML5 video
playback of imageStack movies), PNG, JPEG, TIFF — plus a graceful
``application/octet-stream`` fallback for everything else. ``python-magic``
adds a ~3 MB libmagic system dependency that has to be installed at the
OS layer; not worth it for four signatures.

The byte length probed (``MAGIC_PROBE_BYTES = 12``) is enough to cover
every signature in the table. The TIFF check uses 8 bytes; the MP4
``ftyp`` check needs the box-size prefix at offset 0 + the literal
``ftyp`` at offset 4–7 (8 bytes total but we look at 12 to be safe in
case alignment changes).
"""
from __future__ import annotations

# Probe at least this many bytes from the head of the payload before
# deciding. Every signature in this module fits in <=12 bytes.
MAGIC_PROBE_BYTES = 12

# Default fallback when nothing in the table matches. Browsers show this
# as a download prompt rather than trying to render — safe behavior.
DEFAULT_CONTENT_TYPE = "application/octet-stream"


def detect_content_type(payload_head: bytes) -> str:
    """Return a MIME type by inspecting magic bytes at the head of ``payload``.

    ``payload_head`` should be at least :data:`MAGIC_PROBE_BYTES` bytes; if
    fewer are provided we still try the shorter signatures (PNG / JPEG /
    TIFF) and fall back to :data:`DEFAULT_CONTENT_TYPE` for anything we
    can't identify.

    Recognized signatures:

    | Bytes | Format | Returned MIME |
    |---|---|---|
    | ``00 00 00 ?? 66 74 79 70`` (``ftyp`` at offset 4) | MP4 / ISO BMFF | ``video/mp4`` |
    | ``89 50 4E 47 0D 0A 1A 0A`` | PNG | ``image/png`` |
    | ``FF D8 FF`` | JPEG | ``image/jpeg`` |
    | ``49 49 2A 00`` (little-endian) | TIFF | ``image/tiff`` |
    | ``4D 4D 00 2A`` (big-endian) | TIFF | ``image/tiff`` |

    Anything else returns :data:`DEFAULT_CONTENT_TYPE`. Empty bytes also
    return the default (no peek possible).
    """
    if not payload_head:
        return DEFAULT_CONTENT_TYPE

    # PNG: 8-byte signature is a hard match.
    if payload_head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # JPEG: 3-byte SOI + marker prefix. Covers JFIF, Exif, raw JPEG.
    if payload_head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # TIFF: little-endian or big-endian byte-order mark + magic 42.
    if payload_head.startswith(b"II*\x00") or payload_head.startswith(b"MM\x00*"):
        return "image/tiff"

    # MP4 / ISO Base Media File Format: a 4-byte big-endian box size at
    # offset 0, then the literal ASCII ``ftyp`` at offset 4–7. The box
    # size itself can be anything (file-dependent), so we ignore the
    # first 4 bytes and only check the type tag. This signature also
    # matches MOV (QuickTime) and other ISO BMFF containers; ``video/mp4``
    # is the right guess for the imageStack movie use case and browsers
    # play QuickTime via the same MP4 codepath.
    if len(payload_head) >= 8 and payload_head[4:8] == b"ftyp":
        return "video/mp4"

    return DEFAULT_CONTENT_TYPE
