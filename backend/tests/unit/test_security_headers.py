"""SecurityHeadersMiddleware contract — CSP, framing, sniff guard, HSTS,
plus the optional CSP report endpoint (O2).

The middleware emits a fixed bundle of headers on every response: CSP,
X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
Permissions-Policy, Strict-Transport-Security. We pin the headers so a
future refactor doesn't accidentally drop one — and so the optional
CSP-violation reporting endpoint (CSP_REPORT_URI) is only enabled when
explicitly configured, never accidentally.

The reporting endpoint:
- Adds `report-uri <url>` to CSP (legacy directive — broadest browser
  support, including older browsers that ignore Report-To).
- Adds `report-to csp-endpoint` to CSP (modern Reporting API).
- Adds a `Report-To` response header with a JSON descriptor pointing
  to the same URL under the `csp-endpoint` group name.
- All three are GATED on CSP_REPORT_URI being non-empty. Default
  behavior is unchanged from the pre-O2 middleware.
"""
from __future__ import annotations

import json

from backend.config import get_settings
from backend.middleware.security_headers import SecurityHeadersMiddleware


def _build_middleware(monkeypatch, *, csp_report_uri: str | None = None):  # type: ignore[no-untyped-def]
    """Construct a fresh middleware after applying the env override.

    The middleware caches the CSP string in __init__ from the settings
    at construction time, so we have to clear get_settings's lru_cache
    *before* instantiation.
    """
    if csp_report_uri is not None:
        monkeypatch.setenv("CSP_REPORT_URI", csp_report_uri)
    else:
        monkeypatch.delenv("CSP_REPORT_URI", raising=False)
    get_settings.cache_clear()
    return SecurityHeadersMiddleware(app=None)


def test_csp_has_no_report_directive_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Default deployment must not point at any reporting endpoint —
    we don't want CSP violations leaking to whatever URL was in a stale
    .env. Reporting is opt-in via CSP_REPORT_URI."""
    middleware = _build_middleware(monkeypatch, csp_report_uri="")
    try:
        assert "report-uri" not in middleware._csp
        assert "report-to" not in middleware._csp
    finally:
        get_settings.cache_clear()


def test_csp_carries_report_uri_when_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Legacy `report-uri` directive — broadest browser support.
    Required for older Firefox / Safari versions that don't yet honor
    the modern Reporting API."""
    url = "https://csp-reports.example.test/r"
    middleware = _build_middleware(monkeypatch, csp_report_uri=url)
    try:
        assert f"report-uri {url}" in middleware._csp
    finally:
        get_settings.cache_clear()


def test_csp_carries_report_to_directive_when_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Modern Reporting API — CSP names a group; the matching `Report-To`
    response header maps the group to the actual URL. Browsers that
    support both prefer report-to."""
    middleware = _build_middleware(
        monkeypatch, csp_report_uri="https://csp-reports.example.test/r",
    )
    try:
        assert "report-to csp-endpoint" in middleware._csp
    finally:
        get_settings.cache_clear()


async def test_report_to_header_emits_when_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The Reporting API requires a `Report-To` response header that
    maps the group name (`csp-endpoint`) to the configured URL. Test the
    middleware's per-response header emission (dispatch path), not just
    the cached CSP string."""
    from starlette.requests import Request
    from starlette.responses import Response

    middleware = _build_middleware(
        monkeypatch, csp_report_uri="https://csp-reports.example.test/r",
    )
    try:
        async def _no_call_next(_req: Request) -> Response:
            return Response("ok")

        scope = {
            "type": "http", "method": "GET", "path": "/", "headers": [],
            "query_string": b"", "client": ("1.2.3.4", 12345),
        }
        request = Request(scope)
        response = await middleware.dispatch(request, _no_call_next)
        report_to_header = response.headers.get("Report-To")
        assert report_to_header is not None
        body = json.loads(report_to_header)
        assert body["group"] == "csp-endpoint"
        assert body["max_age"] >= 60  # something sensible (typically days)
        endpoints = body["endpoints"]
        assert isinstance(endpoints, list)
        assert any(
            ep.get("url") == "https://csp-reports.example.test/r" for ep in endpoints
        )
    finally:
        get_settings.cache_clear()


async def test_report_to_header_absent_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """No CSP_REPORT_URI → no Report-To response header."""
    from starlette.requests import Request
    from starlette.responses import Response

    middleware = _build_middleware(monkeypatch, csp_report_uri="")
    try:
        async def _no_call_next(_req: Request) -> Response:
            return Response("ok")

        scope = {
            "type": "http", "method": "GET", "path": "/", "headers": [],
            "query_string": b"", "client": ("1.2.3.4", 12345),
        }
        request = Request(scope)
        response = await middleware.dispatch(request, _no_call_next)
        assert response.headers.get("Report-To") is None
    finally:
        get_settings.cache_clear()


async def test_baseline_security_headers_unchanged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The fixed bundle (CSP, framing, sniff, referrer, permissions, HSTS)
    must continue to ship on every response. Pin them so the report-to
    refactor doesn't accidentally drop a header in transit."""
    from starlette.requests import Request
    from starlette.responses import Response

    middleware = _build_middleware(monkeypatch, csp_report_uri="")
    try:
        async def _no_call_next(_req: Request) -> Response:
            return Response("ok")

        scope = {
            "type": "http", "method": "GET", "path": "/", "headers": [],
            "query_string": b"", "client": ("1.2.3.4", 12345),
        }
        request = Request(scope)
        response = await middleware.dispatch(request, _no_call_next)
        assert response.headers.get("Content-Security-Policy") is not None
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "camera=()" in response.headers.get("Permissions-Policy", "")
        assert "max-age=31536000" in response.headers.get(
            "Strict-Transport-Security", "",
        )
    finally:
        get_settings.cache_clear()
