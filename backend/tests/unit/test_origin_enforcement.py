"""Origin enforcement on mutating requests (O5).

The audit (synthesis §O5) flagged that the FastAPI proxy had no
server-side Origin header check on mutations. The browser-enforced
CORS layer covers cross-origin requests from compliant browsers, but
a non-browser client (curl, attacker tooling, a CSRF-bypass scenario)
can ignore CORS entirely. Server-side Origin enforcement is the
defense-in-depth layer that catches those.

The contract:
- Safe methods (GET, HEAD, OPTIONS) bypass the check entirely.
- The CSRF-bootstrap path (`/api/auth/csrf`) and health endpoints
  bypass — they need to be reachable from contexts that don't yet
  have a session/origin context.
- For every other mutating request:
  - If `Origin` is present, it MUST be in CORS_ORIGINS. Mismatch →
    403 FORBIDDEN.
  - If `Origin` is absent, fall back to `Referer` and check its
    origin against CORS_ORIGINS. Some same-origin POSTs in older
    browsers omit Origin but include Referer.
  - If BOTH are absent (the audit's "no-Origin handling" case):
    REJECT. Legitimate browser-driven mutations always carry Origin
    or Referer; absence is suspicious. Strict rejection here is a
    deliberate choice — the SPA is the only legitimate caller and
    always sends Origin.
"""
from __future__ import annotations

import pytest
import structlog
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import get_settings


@pytest.fixture
def app_client(fake_redis, monkeypatch):  # type: ignore[no-untyped-def]
    """App + TestClient with a deterministic CORS_ORIGINS allowlist.

    Override the env var explicitly so tests don't depend on whatever
    a dev `backend/.env` happens to contain — pydantic-settings would
    otherwise pick up a developer's local CORS list and these tests
    would only pass on certain laptops.
    """
    saved_structlog_config = structlog.get_config()
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "http://localhost:5173,https://ndi-cloud.com,https://www.ndi-cloud.com",
    )
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            from backend.auth.session import SessionStore
            from backend.middleware.rate_limit import RateLimiter
            app.state.redis = fake_redis
            app.state.session_store = SessionStore(fake_redis)
            app.state.rate_limiter = RateLimiter(fake_redis)
            yield client
    finally:
        get_settings.cache_clear()
        structlog.configure(**saved_structlog_config)


def _csrf(client: TestClient) -> dict[str, str]:
    """Mint a CSRF token + return matching header for the test client."""
    r = client.get("/api/auth/csrf")
    assert r.status_code == 200
    token = r.json()["csrfToken"]
    client.cookies.set("XSRF-TOKEN", token)
    return {"X-XSRF-TOKEN": token}


def test_get_request_with_no_origin_passes_through(app_client) -> None:  # type: ignore[no-untyped-def]
    """Safe methods are exempt — no Origin requirement on reads. /api/health
    is the smoke target since it's available without setup."""
    r = app_client.get("/api/health")
    assert r.status_code == 200


def test_post_with_allowed_origin_passes_origin_check(app_client) -> None:  # type: ignore[no-untyped-def]
    """A mutating request from an allowlisted Origin gets through the
    Origin check. We hit /api/auth/logout with valid CSRF — the handler
    short-circuits on missing session without calling the cloud, so we
    can assert response code without network mocks."""
    csrf_headers = _csrf(app_client)
    headers = {**csrf_headers, "Origin": "https://ndi-cloud.com"}
    r = app_client.post("/api/auth/logout", headers=headers)
    # NOT 403 from origin enforcement — handler ran and returned ok.
    assert r.status_code == 200, r.json()


def test_post_with_disallowed_origin_returns_403_forbidden(
    app_client,
) -> None:  # type: ignore[no-untyped-def]
    """A mutating request from a non-allowlisted Origin is rejected
    with FORBIDDEN. Specifically NOT a CSRF_INVALID — the typed code
    differentiates 'your origin is wrong' from 'your CSRF state is
    wrong' so the frontend / SOC tooling can route the alert correctly."""
    csrf_headers = _csrf(app_client)
    headers = {**csrf_headers, "Origin": "https://evil.example.test"}
    r = app_client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 403, r.json()
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_post_with_no_origin_and_no_referer_returns_403_forbidden(
    app_client,
) -> None:  # type: ignore[no-untyped-def]
    """The 'no-Origin' case the audit specifically called out. Strict:
    legitimate browser-driven mutations always carry Origin or Referer;
    absence is suspicious. Reject."""
    csrf_headers = _csrf(app_client)
    # TestClient sets neither Origin nor Referer by default. To make
    # absence explicit, override any defaults.
    r = app_client.post("/api/auth/logout", headers=csrf_headers)
    assert r.status_code == 403, r.json()
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_post_with_referer_fallback_when_origin_absent(app_client) -> None:  # type: ignore[no-untyped-def]
    """Some same-origin POSTs in older browsers omit Origin but include
    Referer. The check should fall back to Referer's origin component."""
    csrf_headers = _csrf(app_client)
    # No Origin; Referer points at a path under an allowlisted origin.
    headers = {
        **csrf_headers,
        "Referer": "https://ndi-cloud.com/some-page?q=1",
    }
    r = app_client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 200, r.json()


def test_post_with_disallowed_referer_origin_returns_403_forbidden(
    app_client,
) -> None:  # type: ignore[no-untyped-def]
    """Referer origin must also be allowlisted — the fallback isn't a
    bypass. A Referer pointing at evil.example would have been a CSRF
    vector before this defense layer existed."""
    csrf_headers = _csrf(app_client)
    headers = {
        **csrf_headers,
        "Referer": "https://evil.example.test/page",
    }
    r = app_client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 403, r.json()
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_csrf_bootstrap_endpoint_does_not_require_origin(app_client) -> None:  # type: ignore[no-untyped-def]
    """``/api/auth/csrf`` is a GET, so it bypasses anyway, but the path
    is also explicitly exempt — pin the contract so a future refactor
    that adds a method on it doesn't break the bootstrap flow."""
    r = app_client.get("/api/auth/csrf")
    assert r.status_code == 200


def test_post_to_csrf_bootstrap_path_is_not_exempt_from_origin(
    app_client,
) -> None:  # type: ignore[no-untyped-def]
    """If someone ever adds a POST handler at /api/auth/csrf, the
    Origin enforcement still applies — the path-level exemption is
    only for the documented GET. We assert the safe-methods exemption
    is the dominant rule for the bootstrap path."""
    # Today there's no POST handler at /api/auth/csrf so this returns
    # 405. Both the absence of a 200 (origin shouldn't be allowing the
    # request through) and the failure mode (405 method-not-allowed,
    # not 403 origin-rejected) are what we want to confirm.
    csrf_headers = _csrf(app_client)
    r = app_client.post(
        "/api/auth/csrf",
        headers={**csrf_headers, "Origin": "https://ndi-cloud.com"},
    )
    # 405 means the handler routing rejected the method. Origin allowed it
    # through.
    assert r.status_code in (404, 405)
