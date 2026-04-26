"""Shared fixtures for integration tests.

Moved out of ``test_routes.py`` so multiple integration test modules can
consume the ``app_and_cloud`` fixture without re-importing (which trips
ruff's F811). Pytest auto-discovers conftest fixtures in the same
directory and below.
"""
from __future__ import annotations

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
            # Override Redis and all Redis-backed state to use fake.
            from backend.auth.session import SessionStore
            from backend.cache.redis_table import RedisTableCache
            from backend.middleware.rate_limit import RateLimiter
            from backend.services.dataset_provenance_service import (
                PROVENANCE_CACHE_TTL_SECONDS,
            )
            from backend.services.dataset_summary_service import (
                SUMMARY_CACHE_TTL_SECONDS,
            )
            from backend.services.facet_service import (
                FACETS_CACHE_TTL_SECONDS,
            )
            from backend.services.pivot_service import PIVOT_CACHE_TTL_SECONDS
            app.state.redis = fake_redis
            app.state.session_store = SessionStore(fake_redis)
            app.state.rate_limiter = RateLimiter(fake_redis)
            app.state.table_cache = RedisTableCache(fake_redis)
            app.state.dataset_summary_cache = RedisTableCache(
                fake_redis, ttl_seconds=SUMMARY_CACHE_TTL_SECONDS,
            )
            app.state.dataset_provenance_cache = RedisTableCache(
                fake_redis, ttl_seconds=PROVENANCE_CACHE_TTL_SECONDS,
            )
            app.state.pivot_cache = RedisTableCache(
                fake_redis, ttl_seconds=PIVOT_CACHE_TTL_SECONDS,
            )
            app.state.facets_cache = RedisTableCache(
                fake_redis, ttl_seconds=FACETS_CACHE_TTL_SECONDS,
            )
            # O5: every mutating request must carry an allowlisted
            # Origin. The conftest sets CORS_ORIGINS so `http://testserver`
            # is allowed; stamp it on the client's default headers so
            # individual tests don't have to thread Origin through every
            # call.
            client.headers["Origin"] = "http://testserver"
            yield client, router
