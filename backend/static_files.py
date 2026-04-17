"""Helpers for serving static files safely.

Extracted from :mod:`backend.app` so the path-containment logic can be
unit-tested without triggering a full FastAPI app creation on import.
"""
from __future__ import annotations

from pathlib import Path


def safe_static_path(root: Path, requested: str) -> Path | None:
    """Return ``root / requested`` iff it resolves to a file inside ``root``.

    Prevents path traversal on the SPA static-fallback route. Starlette
    URL-decodes the incoming path before it reaches the handler, so by the
    time ``requested`` arrives here ``%2e%2e%2f`` has already become
    ``../``. ``pathlib.Path`` division does not normalize parent segments,
    and ``is_file()`` happily reports True for any real file — so without
    an explicit containment check ``(dist / "../../etc/passwd").is_file()``
    returns True and ``FileResponse`` would stream it.

    The safe pattern is: resolve the full candidate, then assert it is
    contained within ``root.resolve()``. ``Path.relative_to`` raises
    ``ValueError`` when containment fails. ``resolve()`` also follows
    symlinks, so a symlink inside ``root`` pointing at an outside file is
    rejected for the same reason.

    Returns ``None`` when:

    - containment check fails (traversal attempt), or
    - the resolved path is not a regular file (directory, missing entry, etc.).

    The caller should fall back to the SPA index in that case rather than
    returning a 404, so the React Router client can present its own
    not-found UI.
    """
    try:
        candidate = (root / requested).resolve()
        candidate.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    if not candidate.is_file():
        return None
    return candidate
