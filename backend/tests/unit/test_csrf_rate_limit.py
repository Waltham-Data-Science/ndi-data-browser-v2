"""CSRF-failure per-IP rate limit (O4).

The audit (synthesis §O4) flagged that CSRF failures returned 403 with
no rate limit. An attacker can spam mutating endpoints with bogus
tokens, consuming cycles, polluting metrics, and obscuring real attack
signal in the noise.

This change adds a per-IP rate limit on CSRF failures. After the Nth
failure (default 20) within a 5-minute sliding window, the same IP
gets 429 + AUTH_RATE_LIMITED instead of the standard 403 CSRF_INVALID,
with a Retry-After-shaped detail. Successful CSRF requests don't burn
budget — only failures count. The bucket is `csrf-fail-ip` so the
metrics surface separates CSRF-fail probes from generic auth limits.
"""
from __future__ import annotations

import pytest
import structlog
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import get_settings


@pytest.fixture
def app_client_with_low_csrf_limit(fake_redis, monkeypatch):  # type: ignore[no-untyped-def]
    """App configured with a 2-failure CSRF-fail budget so tests can
    burn it and the next attempt without hand-rolling 21 requests.

    Snapshots the structlog config before instantiating the app —
    ``create_app()``'s lifespan calls ``configure_logging()`` which
    overwrites structlog state, and that state outlives this fixture
    if not restored. test_dependencies.py's ``capture_logs()`` tests
    rely on structlog being in its capture-friendly default state, so
    we save/restore around the create_app() side effect.
    """
    saved_structlog_config = structlog.get_config()
    monkeypatch.setenv("RATE_LIMIT_CSRF_FAIL_PER_IP_5MIN", "2")
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
        monkeypatch.delenv("RATE_LIMIT_CSRF_FAIL_PER_IP_5MIN", raising=False)
        get_settings.cache_clear()
        # Restore the pre-create_app() structlog config so subsequent
        # tests' ``capture_logs()`` works as expected.
        structlog.configure(**saved_structlog_config)


def test_first_csrf_failure_returns_403_typed(app_client_with_low_csrf_limit) -> None:  # type: ignore[no-untyped-def]
    """A single CSRF failure (no token) returns the existing typed 403,
    unchanged from pre-O4 behavior. Rate limit only kicks in on the
    Nth+1 failure."""
    r = app_client_with_low_csrf_limit.post("/api/query", json={})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_repeated_csrf_failures_eventually_return_429_typed(
    app_client_with_low_csrf_limit,
) -> None:  # type: ignore[no-untyped-def]
    """Burn the per-IP CSRF-fail budget (configured to 2 in this test).
    The 3rd failure must return 429 + AUTH_RATE_LIMITED, NOT another
    403 CSRF_INVALID — the typed code shift is what lets the frontend
    distinguish 'your CSRF state is out of sync' from 'you are being
    rate-limited because your client looks like a probe'."""
    # Burn the budget of 2 failures.
    for _ in range(2):
        r = app_client_with_low_csrf_limit.post("/api/query", json={})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "CSRF_INVALID"
    # Third attempt — rejected by the rate limiter.
    r = app_client_with_low_csrf_limit.post("/api/query", json={})
    assert r.status_code == 429, r.json()
    assert r.json()["error"]["code"] == "AUTH_RATE_LIMITED"


def test_successful_csrf_does_not_burn_budget(
    app_client_with_low_csrf_limit,
) -> None:  # type: ignore[no-untyped-def]
    """A request that PASSES CSRF (token + cookie present + matched +
    signed) doesn't count against the failure budget. The whole point
    of the limit is to stop ABUSE — legitimate requests must not be
    handicapped.

    We use /api/auth/logout as the post-CSRF target because it's a
    mutating endpoint (POST), CSRF-gated, and the handler short-circuits
    on missing session without calling the cloud. So a no-session +
    valid-CSRF POST gives us a deterministic non-CSRF response we can
    assert against without network mocks.
    """
    # Mint a token via /api/auth/csrf and use it on subsequent mutations.
    r = app_client_with_low_csrf_limit.get("/api/auth/csrf")
    assert r.status_code == 200
    csrf_token = r.json()["csrfToken"]
    # Belt-and-suspenders: TestClient should auto-persist the cookie
    # from /api/auth/csrf, but make it explicit so the test doesn't
    # depend on TestClient's cookie-jar behavior.
    app_client_with_low_csrf_limit.cookies.set("XSRF-TOKEN", csrf_token)
    headers = {"X-XSRF-TOKEN": csrf_token}
    # Make 3 SUCCESSFUL CSRF requests (the budget is 2 — if successful
    # CSRF burned budget, the third would already 429). The handler
    # responds without calling the cloud since there's no session.
    for _ in range(3):
        r = app_client_with_low_csrf_limit.post("/api/auth/logout", headers=headers)
        # Whatever the response code, it MUST NOT be a CSRF failure —
        # our token was valid.
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            assert body["error"]["code"] != "CSRF_INVALID"
    # Now make a CSRF-failing request — should be 403 (budget unburned
    # by the three successful requests above).
    r_fail = app_client_with_low_csrf_limit.post("/api/auth/logout")
    assert r_fail.status_code == 403
    assert r_fail.json()["error"]["code"] == "CSRF_INVALID"
