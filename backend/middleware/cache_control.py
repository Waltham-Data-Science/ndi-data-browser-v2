"""Cache-Control + ETag middleware.

Opt-in conditional-GET caching for read endpoints. Goals:

1. Browsers revisiting the same URL get a ``304 Not Modified`` when
   the response body hasn't changed — zero bytes transferred after
   the first paint.
2. Cacheable responses advertise a short freshness window via
   ``Cache-Control`` so the browser skips the network entirely for
   the window's duration (TanStack Query's client cache stacks on
   top of this, but the browser HTTP cache is the fallback when the
   SPA cold-starts).

Only ``GET`` methods on ``/api/*`` paths get ETags + Cache-Control.
Mutation endpoints and non-API responses pass through untouched.

Public vs private
─────────────────
The middleware reads ``request.state.user_id`` (set upstream by
``get_current_session`` when the session cookie validates) to pick
the Cache-Control visibility:

- Authenticated request (``request.state.user_id`` present):
  ``Cache-Control: private, max-age=<n>`` — browser caches, CDNs
  (Cloudflare / Fastly) must NOT.
- Unauthenticated request:
  ``Cache-Control: public, max-age=<n>, s-maxage=<n>`` — both the
  browser AND CDN can cache. This is the same contract
  ``ndi-cloud-node``'s ``publicCacheable`` helper uses.

The `max-age` default is 60s (short enough that dataset publish
events surface within a minute). Increase per-route by setting the
``cacheable_seconds`` attribute on the response via
``response.headers['X-Cache-Max-Age'] = '300'`` inside the handler,
which this middleware strips after consumption.

Why ASGI wrapping instead of BaseHTTPMiddleware
───────────────────────────────────────────────
BaseHTTPMiddleware buffers the entire response into memory as a
BytesIO via Starlette's `_CachedRequest` — which is fine for JSON
responses but doesn't work for StreamingResponse / FileResponse
(which we use for SPA index.html serving). Writing this as raw ASGI
middleware lets us wrap only JSON responses and pass everything else
through unmolested.
"""
from __future__ import annotations

import hashlib
from typing import Any, cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Routes that shouldn't get ETag/Cache-Control — auth flows and
# anything that sets cookies must always vary.
_NEVER_CACHE_PREFIXES = (
    "/api/auth/",
    "/api/health",
    "/metrics",
)

# Default freshness — 60 seconds. Individual handlers can tune up or
# down via the `X-Cache-Max-Age` response header (consumed + stripped
# by this middleware).
_DEFAULT_MAX_AGE = 60


class CacheControlMiddleware:
    """ASGI middleware. Adds ETag + Cache-Control to cacheable GETs
    and handles If-None-Match for 304 responses.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        path = scope.get("path", "")
        if method != "GET" or not path.startswith("/api/") or _never_cache(path):
            await self.app(scope, receive, send)
            return

        # Check If-None-Match early so we can short-circuit if possible.
        inm = _header_get(scope, b"if-none-match") or ""

        # We intercept the response to compute an ETag over the body
        # and rewrite headers before forwarding. Body chunks are
        # buffered — fine because our JSON responses are <1MB.
        body_parts: list[bytes] = []
        response_start: Message | None = None
        short_circuited = False

        async def _send(message: Message) -> None:
            nonlocal response_start, short_circuited
            if message["type"] == "http.response.start":
                response_start = message
                return
            if message["type"] == "http.response.body":
                # Buffer body chunks; we need the full body to compute
                # the ETag. `more_body=False` means last chunk.
                body_parts.append(cast(bytes, message.get("body") or b""))
                if message.get("more_body", False):
                    return
                # Full body received. Compute ETag + decide headers.
                assert response_start is not None
                status = cast(int, response_start.get("status", 200))
                headers = _headers_to_list(response_start)

                # Only attach ETag to 200-ish JSON responses. Redirects,
                # 404s, 5xxs should pass through without caching since
                # the bodies are error envelopes we don't want cached.
                if status != 200 or not _is_json_response(headers):
                    await send(response_start)
                    await send(_reassembled_body(body_parts))
                    short_circuited = True
                    return

                body = b"".join(body_parts)
                etag = _compute_etag(body)

                # Conditional GET hit — client already has this exact
                # body. Send 304 Not Modified with the same cache
                # headers but an empty body.
                if inm and _etag_matches(inm, etag):
                    cache_header = _cache_control_for(scope, headers)
                    h304: list[tuple[bytes, bytes]] = [
                        (b"etag", etag.encode("ascii")),
                        (b"cache-control", cache_header.encode("ascii")),
                    ]
                    # Preserve request-id / vary if upstream set them.
                    for k, v in headers:
                        lk = k.lower()
                        if lk in (b"x-request-id", b"vary"):
                            h304.append((k, v))
                    await send({
                        "type": "http.response.start",
                        "status": 304,
                        "headers": h304,
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b"",
                        "more_body": False,
                    })
                    short_circuited = True
                    return

                # Regular 200 — attach ETag + Cache-Control, strip the
                # sentinel X-Cache-Max-Age if the handler set one, and
                # forward body as-is.
                new_headers = _strip_header(headers, b"x-cache-max-age")
                new_headers.append((b"etag", etag.encode("ascii")))
                new_headers.append((
                    b"cache-control",
                    _cache_control_for(scope, headers).encode("ascii"),
                ))
                response_start["headers"] = new_headers

                await send(response_start)
                await send({
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                })
                short_circuited = True
                return

        await self.app(scope, receive, _send)
        if not short_circuited and response_start is not None:
            # Shouldn't happen — streaming responses are filtered out
            # above. Defensive: flush whatever we captured.
            await send(response_start)
            await send(_reassembled_body(body_parts))


# ─── Helpers ─────────────────────────────────────────────────────


def _never_cache(path: str) -> bool:
    return any(path.startswith(p) for p in _NEVER_CACHE_PREFIXES)


def _header_get(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers", []):
        if k.lower() == name:
            # Scope["headers"] is typed as Iterable[tuple[Any, Any]] —
            # the ASGI contract guarantees bytes, so the decode call is
            # safe but mypy doesn't see that. cast to keep strict mode
            # happy without runtime overhead.
            return cast(bytes, v).decode("latin-1")
    return None


def _headers_to_list(message: Message) -> list[tuple[bytes, bytes]]:
    raw = message.get("headers") or []
    return [(k, v) for k, v in raw]


def _strip_header(
    headers: list[tuple[bytes, bytes]], name: bytes,
) -> list[tuple[bytes, bytes]]:
    return [(k, v) for k, v in headers if k.lower() != name]


def _is_json_response(headers: list[tuple[bytes, bytes]]) -> bool:
    for k, v in headers:
        if k.lower() == b"content-type":
            return b"application/json" in v.lower()
    return False


def _compute_etag(body: bytes) -> str:
    # Weak ETag — fine for our use: we care about byte-equality of
    # responses, not security. sha256 first 16 chars is cheap and
    # has negligible collision risk across typical response bodies.
    digest = hashlib.sha256(body).hexdigest()[:16]
    return f'W/"{digest}"'


def _etag_matches(inm: str, etag: str) -> bool:
    # Browsers send If-None-Match as a comma-separated list, optionally
    # including W/ weak prefix. Normalize both sides for comparison.
    candidates = [c.strip() for c in inm.split(",")]
    strong = etag.replace("W/", "")
    return any(c in (etag, strong) for c in candidates)


def _cache_control_for(
    scope: Scope, headers: list[tuple[bytes, bytes]],
) -> str:
    # Handler can request a specific max-age via the sentinel header.
    handler_max_age: int | None = None
    for k, v in headers:
        if k.lower() == b"x-cache-max-age":
            try:
                handler_max_age = int(v.decode("ascii").strip())
            except ValueError:
                handler_max_age = None
            break
    max_age = handler_max_age if handler_max_age is not None else _DEFAULT_MAX_AGE

    # `request.state.user_id` is set by get_current_session on a valid
    # session. Starlette stores request.state inside scope as a
    # pseudo-attribute — we can't read it here without the full
    # Request object, so instead we read the session cookie presence
    # as a proxy for "authenticated request".
    is_authed = _has_session_cookie(scope)
    if is_authed:
        return f"private, max-age={max_age}"
    return f"public, max-age={max_age}, s-maxage={max_age}"


def _has_session_cookie(scope: Scope) -> bool:
    cookie = _header_get(scope, b"cookie") or ""
    # Our session cookie is named `ndi_session` (defined in
    # auth/session.py). Presence alone doesn't prove validity — but
    # for cache-visibility purposes it's the right proxy: if the
    # client sent the cookie, CDNs must not share the response.
    return any(
        part.strip().startswith("ndi_session=") for part in cookie.split(";")
    )


def _reassembled_body(parts: list[bytes]) -> Message:
    return {
        "type": "http.response.body",
        "body": b"".join(parts),
        "more_body": False,
    }


# ─── Public helper for route handlers ─────────────────────────────


def set_cache_max_age(response: Any, seconds: int) -> None:
    """Hint from a route handler: set a custom max-age on this
    response. The middleware consumes and strips the header before
    sending.

    Example::

        @router.get("/class-counts")
        async def class_counts(response: Response, ...):
            data = await svc.compute(...)
            set_cache_max_age(response, 300)  # 5 min — class counts rarely change
            return data
    """
    response.headers["X-Cache-Max-Age"] = str(int(seconds))
