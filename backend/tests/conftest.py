"""Shared pytest fixtures."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
import respx
import structlog
from cryptography.fernet import Fernet

os.environ.setdefault("NDI_CLOUD_URL", "https://api.example.test/v1")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("CSRF_SIGNING_KEY", "a" * 64)
os.environ.setdefault("ENVIRONMENT", "development")
# Override CORS_ORIGINS so the test harness has a deterministic
# allowlist regardless of any local `backend/.env` (which the dev
# `.env` mechanism would otherwise fold in via pydantic-settings).
# `http://testserver` is the default Origin we stamp onto integration
# test clients (see integration/conftest.py) so the O5 origin
# enforcement middleware accepts test requests.
os.environ.setdefault(
    "CORS_ORIGINS",
    "http://testserver,http://localhost:5173,https://ndi-cloud.com,https://www.ndi-cloud.com",
)


@pytest.fixture(autouse=True)
def _reset_structlog_for_capture() -> None:
    """Stream 6.6 fix (2026-05-15) — pretest isolation for structlog.

    Several tests use ``structlog.testing.capture_logs()`` to assert that
    a specific event was emitted. `capture_logs` is a context manager that
    activates an in-memory processor — but only for log calls made through
    the global structlog config it sees at __enter__ time. If a prior test
    re-configured structlog (via ``backend.observability.logging.configure_logging``
    or test-local ``structlog.configure(...)``), the cached ``BoundLogger``
    instances created at module-import time no longer point at the capture
    processor and emit through the pre-existing chain instead. The visible
    symptom: the WARNING log line is captured by stdlib logging (see the
    ``Captured log call`` section in pytest output) but the
    ``logs`` list passed to the test is empty.

    Fix:
      1. ``reset_defaults()`` — undo any prior ``structlog.configure(...)``
         call so the loggers fall back to fresh defaults.
      2. ``configure(... cache_logger_on_first_use=False ...)`` — re-bind
         with caching DISABLED so future ``get_logger(...)`` calls (and the
         module-level cached references) resolve through the current
         processor chain on every emit, picking up ``capture_logs``'s
         in-memory processor when it's active.

    The three pretest-isolation flakes this closes:
      - test_cloud_client.py::test_download_from_off_allowlist_host_hard_rejects
      - test_cloud_client.py::test_download_non_http_scheme_rejected
      - test_origin_enforcement.py::test_post_with_disallowed_referer_origin_returns_403_forbidden
    """
    structlog.reset_defaults()
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )


@pytest.fixture
async def fake_redis() -> AsyncIterator[Any]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def respx_cloud() -> AsyncIterator[respx.MockRouter]:
    with respx.mock(base_url="https://api.example.test/v1", assert_all_called=False) as router:
        yield router


@pytest.fixture
async def cloud_client():
    from backend.clients.ndi_cloud import NdiCloudClient
    c = NdiCloudClient()
    await c.start()
    yield c
    await c.close()
