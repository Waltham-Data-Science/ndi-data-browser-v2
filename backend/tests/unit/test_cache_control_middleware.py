"""Unit tests for CacheControlMiddleware (audit 2026-04-23, issue #50).

Covers the authentication-sensitivity of the emitted ``Cache-Control`` +
``Vary`` headers. Before this audit the middleware checked for a cookie
named ``ndi_session=`` but the actual session cookie is named ``session=``
— so every authenticated response was stamped ``public, s-maxage=60`` and
no ``Vary: Cookie`` was set. Any shared cache (Cloudflare, corporate MITM
proxy) would leak one user's response to every other user. These tests
assert:

1. Authenticated requests get ``private, max-age=...`` (no ``s-maxage``).
2. Unauthenticated requests get ``public, max-age=..., s-maxage=...``.
3. A request carrying the OLD (wrong) ``ndi_session=`` cookie is NOT
   treated as authenticated — it must get the public policy, since the
   cookie isn't real.
4. ``Vary: Cookie, Accept-Encoding`` is always emitted on cacheable
   responses (defense-in-depth against any future cookie-name drift).
5. ``Vary`` is emitted on 304 Not Modified responses too.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.middleware.cache_control import CacheControlMiddleware


def _mk_scope(
    *,
    path: str = "/api/datasets/ds1",
    cookie: str | None = None,
    if_none_match: str | None = None,
) -> dict:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", cookie.encode("latin-1")))
    if if_none_match is not None:
        headers.append((b"if-none-match", if_none_match.encode("ascii")))
    return {
        "type": "http.http",  # placeholder — middleware checks "http" prefix
        "method": "GET",
        "path": path,
        "headers": headers,
    }


async def _run_middleware(
    scope: dict, body: bytes, content_type: bytes = b"application/json",
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Run the middleware against a fake ASGI app that emits the given body.

    Returns the (status, headers, body) of whatever the middleware sends
    downstream.
    """
    scope = {**scope, "type": "http"}

    async def fake_app(scope, receive, send):  # type: ignore[no-untyped-def]
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", content_type)],
        })
        await send({
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        })

    middleware = CacheControlMiddleware(fake_app)

    out_status = {"code": 0}
    out_headers: list[tuple[bytes, bytes]] = []
    out_body = bytearray()

    async def receive():  # type: ignore[no-untyped-def]
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # type: ignore[no-untyped-def]
        if message["type"] == "http.response.start":
            out_status["code"] = message["status"]
            out_headers.extend(message["headers"])
        elif message["type"] == "http.response.body":
            out_body.extend(message.get("body") or b"")

    await middleware(scope, receive, send)
    return out_status["code"], out_headers, bytes(out_body)


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for k, v in headers:
        if k.lower() == name:
            return v.decode("latin-1")
    return None


@pytest.mark.asyncio
async def test_authenticated_request_gets_private_cache_control() -> None:
    """Issue #50 fix: session cookie is named `session=`, not `ndi_session=`."""
    scope = _mk_scope(cookie="session=abc123; other=1")
    status, headers, body = await _run_middleware(scope, b'{"ok":true}')
    assert status == 200
    cc = _header(headers, b"cache-control") or ""
    assert "private" in cc
    assert "s-maxage" not in cc, (
        "authenticated responses must NOT carry s-maxage "
        "(that would let shared CDNs cache per-user data)"
    )


@pytest.mark.asyncio
async def test_unauthenticated_request_gets_public_cache_control() -> None:
    scope = _mk_scope(cookie=None)
    status, headers, body = await _run_middleware(scope, b'{"ok":true}')
    assert status == 200
    cc = _header(headers, b"cache-control") or ""
    assert "public" in cc
    assert "s-maxage=" in cc


@pytest.mark.asyncio
async def test_old_wrong_cookie_name_is_not_treated_as_authenticated() -> None:
    """Historical drift: the middleware used to check `ndi_session=`. If
    someone ships that cookie name again, it must not spoof authentication."""
    scope = _mk_scope(cookie="ndi_session=abc123")
    status, headers, body = await _run_middleware(scope, b'{"ok":true}')
    assert status == 200
    cc = _header(headers, b"cache-control") or ""
    # The `session=` check is strict — `ndi_session=` should NOT match.
    # If it did (as the old bug allowed), this would get `private` and
    # never set `s-maxage`, which is the direction we DON'T want.
    assert "public" in cc
    assert "s-maxage=" in cc


@pytest.mark.asyncio
async def test_vary_header_always_set_on_cacheable_response() -> None:
    """Defense in depth: Vary: Cookie must be present even if the cookie
    check itself drifts again. Shared caches segmenting on Cookie can't
    cross-contaminate users."""
    # Unauthenticated
    scope = _mk_scope(cookie=None)
    _, headers, _ = await _run_middleware(scope, b'{"ok":true}')
    vary = _header(headers, b"vary") or ""
    assert "Cookie" in vary
    assert "Accept-Encoding" in vary

    # Authenticated
    scope = _mk_scope(cookie="session=abc123")
    _, headers, _ = await _run_middleware(scope, b'{"ok":true}')
    vary = _header(headers, b"vary") or ""
    assert "Cookie" in vary


@pytest.mark.asyncio
async def test_304_response_also_carries_vary_cookie() -> None:
    body = b'{"same":"body"}'
    # First fetch to discover the ETag.
    scope = _mk_scope(cookie="session=abc123")
    _, headers1, _ = await _run_middleware(scope, body)
    etag = _header(headers1, b"etag")
    assert etag is not None

    # Second fetch with If-None-Match — should short-circuit to 304.
    scope = _mk_scope(cookie="session=abc123", if_none_match=etag)
    status, headers2, body2 = await _run_middleware(scope, body)
    assert status == 304
    assert body2 == b""
    vary = _header(headers2, b"vary") or ""
    assert "Cookie" in vary, "304 response must also Vary on Cookie"
    cc = _header(headers2, b"cache-control") or ""
    assert "private" in cc, "304 for authed client must be private"


@pytest.mark.asyncio
async def test_never_cache_paths_bypass_middleware() -> None:
    """Auth routes are in _NEVER_CACHE_PREFIXES — no ETag/Cache-Control."""
    scope = _mk_scope(path="/api/auth/me", cookie="session=abc123")
    status, headers, _ = await _run_middleware(scope, b'{"user":"a"}')
    assert status == 200
    # Middleware should NOT have injected cache headers.
    assert _header(headers, b"cache-control") is None
    assert _header(headers, b"etag") is None
