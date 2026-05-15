"""Tests for the cookie_attrs helper.

The helper centralizes the per-environment Set-Cookie/Delete-Cookie
attribute derivation that the backend uses for the session and CSRF
cookies. Two layers:

  - **Environment** decides whether ``Secure`` is set and whether
    ``Domain=.ndi-cloud.com`` is even considered:
        - production: Domain is conditional (see next layer)
        - development: no Secure, no Domain (plain-HTTP localhost)
        - staging / other: Secure, no Domain (host-only)

  - **Per-request Origin** decides whether ``Domain=.ndi-cloud.com``
    is actually attached in production. The apex Vercel deployment
    needs it so the Railway backend's cookies are readable on the
    apex host. Vercel preview deployments at ``*.vercel.app`` need
    it OMITTED — otherwise the browser silently rejects the Set-
    Cookie because the response origin doesn't match the cookie's
    claimed Domain (this was the 2026-05-14 preview-login CSRF
    failure).
"""
from typing import Literal

from fastapi import Request

from backend.auth.cookie_attrs import cookie_attrs
from backend.config import Settings

EnvName = Literal["development", "staging", "production"]


def _settings(env: EnvName) -> Settings:
    # Other required fields (NDI_CLOUD_URL, REDIS_URL, etc.) come from
    # the env vars set in backend/tests/conftest.py.
    return Settings(ENVIRONMENT=env)


def _request(origin: str | None = None, referer: str | None = None) -> Request:
    """Build a minimal Starlette Request for cookie_attrs to read.

    Only the headers matter for this helper — scope.path/method are
    unused. Using a raw scope avoids pulling in the TestClient just
    to get a Request instance.
    """
    headers: list[tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/api/auth/csrf",
            "headers": headers,
            "query_string": b"",
        }
    )


# ─── Production × apex origin → Domain attached ─────────────────────────

def test_production_with_apex_origin_attaches_domain() -> None:
    """The original cross-repo unification (Phase 4) contract."""
    attrs = cookie_attrs(
        _settings("production"),
        request=_request(origin="https://ndi-cloud.com"),
    )
    assert attrs == {"secure": True, "domain": ".ndi-cloud.com"}


def test_production_with_subdomain_origin_attaches_domain() -> None:
    """`app.ndi-cloud.com` (legacy) and any future `*.ndi-cloud.com`."""
    attrs = cookie_attrs(
        _settings("production"),
        request=_request(origin="https://app.ndi-cloud.com"),
    )
    assert attrs == {"secure": True, "domain": ".ndi-cloud.com"}


def test_production_with_referer_only_attaches_domain() -> None:
    """Same-origin GETs may omit Origin; Referer should still work."""
    attrs = cookie_attrs(
        _settings("production"),
        request=_request(referer="https://ndi-cloud.com/login"),
    )
    assert attrs == {"secure": True, "domain": ".ndi-cloud.com"}


# ─── Production × preview / unknown origin → host-only ──────────────────

def test_production_with_vercel_preview_origin_is_host_only() -> None:
    """The 2026-05-14 preview-login fix: no Domain attribute when
    the request came from a Vercel preview hostname."""
    attrs = cookie_attrs(
        _settings("production"),
        request=_request(origin="https://ndi-cloud-app-web-git-feat-x.vercel.app"),
    )
    assert attrs == {"secure": True}
    assert "domain" not in attrs


def test_production_with_no_origin_or_referer_is_host_only() -> None:
    """Fail-safe path: when we can't tell where the request came
    from, drop Domain. Worse case is host-only cookies on apex (which
    still work — they just don't cross-subdomain share)."""
    attrs = cookie_attrs(_settings("production"), request=_request())
    assert attrs == {"secure": True}
    assert "domain" not in attrs


def test_production_with_unrelated_origin_is_host_only() -> None:
    """Origin is `https://attacker.example` — don't attach our apex
    Domain to that response (browsers would reject anyway, but be
    explicit)."""
    attrs = cookie_attrs(
        _settings("production"),
        request=_request(origin="https://attacker.example"),
    )
    assert attrs == {"secure": True}
    assert "domain" not in attrs


# ─── Non-production envs: Origin doesn't matter ─────────────────────────

def test_development_returns_insecure_without_domain() -> None:
    """Localhost over plain HTTP needs Secure=False; Domain is
    irrelevant either way."""
    attrs = cookie_attrs(_settings("development"), request=_request())
    assert attrs == {"secure": False}
    assert "domain" not in attrs


def test_staging_returns_secure_without_domain() -> None:
    """Staging serves over HTTPS but host-only."""
    attrs = cookie_attrs(
        _settings("staging"),
        request=_request(origin="https://ndi-cloud.com"),
    )
    assert attrs == {"secure": True}
    assert "domain" not in attrs
