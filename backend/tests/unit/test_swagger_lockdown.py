"""Swagger / ReDoc / OpenAPI exposure gating (B7).

In production we don't want to publish the full live OpenAPI spec — it
hands an attacker a free map of every route, every Pydantic body shape,
and every error envelope. The audit (synthesis §B7) called this out as
a cutover blocker.

These tests pin the contract:
- production: `docs_url`, `redoc_url`, `openapi_url` are all `None`,
  meaning FastAPI registers no Swagger UI / ReDoc / spec route at all.
- development (and staging): the routes stay enabled so contributors
  and integration tests can still introspect the API.

We test the FastAPI app attributes rather than HTTP status codes
because in production the SPA fallback (`backend/app.py:389-405`)
catches anything not consumed by an /api/* router and returns
`index.html`. That means a `GET /docs` in prod would 200-with-HTML
even with docs disabled — the meaningful guarantee is that FastAPI
itself never publishes the spec route.
"""
from __future__ import annotations

from backend.config import get_settings


def test_swagger_disabled_in_production(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        from backend.app import create_app
        app = create_app()
        assert app.docs_url is None, "Swagger UI must be disabled in production (B7)"
        assert app.redoc_url is None, "ReDoc must be disabled in production (B7)"
        assert app.openapi_url is None, "OpenAPI spec endpoint must be disabled in production (B7)"
    finally:
        get_settings.cache_clear()


def test_swagger_enabled_in_development(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ENVIRONMENT", "development")
    get_settings.cache_clear()
    try:
        from backend.app import create_app
        app = create_app()
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"
    finally:
        get_settings.cache_clear()


def test_swagger_enabled_in_staging(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Staging keeps docs on so QA can introspect — only production hides them."""
    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()
    try:
        from backend.app import create_app
        app = create_app()
        assert app.docs_url == "/docs"
        assert app.openapi_url == "/openapi.json"
    finally:
        get_settings.cache_clear()
