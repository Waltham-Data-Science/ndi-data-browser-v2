"""Shared pytest fixtures."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
import respx
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
