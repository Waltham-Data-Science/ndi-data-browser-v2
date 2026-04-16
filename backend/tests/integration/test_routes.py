"""End-to-end FastAPI routing with respx-mocked cloud + fakeredis."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.fixture
def app_and_cloud(fake_redis):  # type: ignore[no-untyped-def]
    with respx.mock(base_url="https://api.example.test/v1", assert_all_called=False) as router:
        app = create_app()
        # Inject test fixtures onto app.state by running the lifespan manually.
        with TestClient(app) as client:
            # Override Redis and session store to use fake.
            from backend.auth.session import SessionStore
            from backend.middleware.rate_limit import RateLimiter
            app.state.redis = fake_redis
            app.state.session_store = SessionStore(fake_redis)
            app.state.rate_limiter = RateLimiter(fake_redis)
            yield client, router


def test_health(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_csrf_endpoint_sets_cookie(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/auth/csrf")
    assert r.status_code == 200
    body = r.json()
    assert "csrfToken" in body
    assert "XSRF-TOKEN" in r.headers.get("set-cookie", "")


def test_me_without_session_returns_401_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert body["error"]["recovery"] == "login"


def test_csrf_missing_on_mutation_returns_403_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.post("/api/query", json={"searchstructure": [], "scope": "public"})
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "CSRF_INVALID"


def test_published_datasets_calls_cloud(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200, json={"totalNumber": 1, "datasets": [{"id": "d1", "name": "Test"}]},
    )
    r = client.get("/api/datasets/published")
    assert r.status_code == 200
    assert r.json()["datasets"][0]["name"] == "Test"


def test_request_id_echoed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/health", headers={"X-Request-ID": "test-id-1234"})
    assert r.headers["X-Request-ID"] == "test-id-1234"


def test_security_headers_present(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/health")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in r.headers


def test_metrics_endpoint(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    # Make a request so metrics have data.
    client.get("/api/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert b"ndb_http_requests_total" in r.content


def test_unknown_route_returns_not_found_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/doesnotexist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND"
