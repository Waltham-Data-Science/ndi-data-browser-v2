"""Regression tests for safe_static_path (SPA fallback path-traversal guard).

Pinned against the live exploit verified on prod 2026-04-17: ``curl
https://ndb-v2-production.up.railway.app/..%2f..%2fetc%2fpasswd`` returned
``/etc/passwd``. Starlette's ``PathConvertor`` URL-decodes ``%2e%2e%2f`` to
``../`` before the handler sees it, so from the handler's POV the payload is
just a relative path. The guard lives in :mod:`backend.static_files`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.static_files import safe_static_path


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A realistic mini `dist/` layout plus an outside-dist "secret" file."""
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html>")
    (root / "favicon.ico").write_bytes(b"\x00\x00\x01")
    (root / "robots.txt").write_text("User-agent: *\n")
    assets = root / "assets"
    assets.mkdir()
    (assets / "app.abc123.js").write_text("// js bundle")
    (assets / "app.abc123.css").write_text("/* css */")
    # Outside-of-dist file that traversal attempts target.
    (tmp_path / "secret.env").write_text("SESSION_ENCRYPTION_KEY=leakme\n")
    (tmp_path / "config.py").write_text('"""sensitive source"""\n')
    return root


# ---- Allowed paths ----------------------------------------------------------


def test_allows_root_file(dist: Path) -> None:
    target = safe_static_path(dist, "favicon.ico")
    assert target is not None
    assert target.name == "favicon.ico"
    assert target.is_file()


def test_allows_robots_txt(dist: Path) -> None:
    target = safe_static_path(dist, "robots.txt")
    assert target is not None
    assert target.read_text().startswith("User-agent")


def test_allows_nested_asset(dist: Path) -> None:
    target = safe_static_path(dist, "assets/app.abc123.js")
    assert target is not None
    assert target.parent.name == "assets"


# ---- Containment failures (core regression) --------------------------------


def test_rejects_single_parent_traversal(dist: Path) -> None:
    # `dist/../secret.env` → resolves outside dist.
    assert safe_static_path(dist, "../secret.env") is None


def test_rejects_double_parent_traversal(dist: Path) -> None:
    assert safe_static_path(dist, "../../etc/passwd") is None


def test_rejects_live_prod_payload(dist: Path) -> None:
    """The exact decoded form of the payload used against prod 2026-04-17."""
    # `%2f..%2fetc%2fpasswd` URL-decodes to `/../etc/passwd`; Starlette strips
    # the leading slash via PathConvertor, so the handler sees `../etc/passwd`.
    assert safe_static_path(dist, "../etc/passwd") is None


def test_rejects_traversal_through_real_prefix(dist: Path) -> None:
    # `assets/` is a real subdir; traversal starting from inside it still escapes.
    assert safe_static_path(dist, "assets/../../etc/passwd") is None


def test_rejects_absolute_path(dist: Path) -> None:
    """``Path(/x) / "/etc/passwd"`` discards the left — still escapes dist."""
    assert safe_static_path(dist, "/etc/passwd") is None


def test_rejects_sibling_backend_source(dist: Path) -> None:
    """The specific exploit category `/cso` demonstrated — backend source read."""
    assert safe_static_path(dist, "../config.py") is None


# ---- Non-file, non-existent paths -------------------------------------------


def test_returns_none_for_missing_file_in_dist(dist: Path) -> None:
    """Caller falls back to index.html for unknown-route client paths."""
    assert safe_static_path(dist, "some/client/route") is None


def test_returns_none_for_empty_path(dist: Path) -> None:
    # Resolves to `dist` itself (a directory).
    assert safe_static_path(dist, "") is None


def test_returns_none_for_dot_path(dist: Path) -> None:
    # Also resolves to `dist` itself.
    assert safe_static_path(dist, ".") is None


def test_returns_none_for_directory(dist: Path) -> None:
    # `assets` is a real directory, not a file; not servable.
    assert safe_static_path(dist, "assets") is None


# ---- Symlink safety ---------------------------------------------------------


def test_rejects_symlink_pointing_outside(dist: Path, tmp_path: Path) -> None:
    """A symlink inside dist pointing at an outside file must not serve it.

    ``resolve()`` follows symlinks, so the containment check on the target
    path catches this without additional logic.
    """
    outside = tmp_path / "secret.env"
    link = dist / "escape.html"
    link.symlink_to(outside)
    assert safe_static_path(dist, "escape.html") is None


def test_allows_symlink_inside_dist(dist: Path) -> None:
    """Sanity check: symlink to a file inside dist is fine."""
    link = dist / "favicon-alias.ico"
    link.symlink_to(dist / "favicon.ico")
    target = safe_static_path(dist, "favicon-alias.ico")
    assert target is not None
    # resolve() follows the link to the real target.
    assert target.name == "favicon.ico"
