"""Auth dependency unit tests (ADR-008 — no refresh path).

These cover the behavior of `get_current_session` / `require_session` now
that the Cognito refresh scaffolding has been deleted: an expired access
token drops the session and surfaces as AuthRequired, with zero attempts
to call `cloud.refresh`.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.auth.dependencies import (
    SESSION_COOKIE,
    get_current_session,
    require_session,
)
from backend.auth.session import SessionStore
from backend.errors import AuthRequired


def _mock_request(*, cookies: dict[str, str] | None = None, app_state: object | None = None):
    cookies = cookies or {}
    state = app_state or SimpleNamespace()
    app = SimpleNamespace(state=state)
    return SimpleNamespace(cookies=cookies, app=app)


@pytest.mark.asyncio
async def test_expired_access_token_raises_auth_required_no_refresh_attempt(
    fake_redis,  # type: ignore[no-untyped-def]
) -> None:
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u1",
        email="alice@example.com",
        access_token="at-expired",
        access_token_expires_in_seconds=60,
        ip="1.1.1.1",
        user_agent="pytest",
    )
    # Force expiry: backdate the stored expires_at well into the past.
    session.access_token_expires_at = int(time.time()) - 10
    await store._write(session)

    # Guardrail: assert the attribute does not exist (ADR-008).
    from backend.clients import ndi_cloud as nc_mod
    assert not hasattr(nc_mod.NdiCloudClient, "refresh"), (
        "NdiCloudClient.refresh must stay deleted per ADR-008"
    )

    # A mock cloud is injected into the request just so that any accidental
    # attempt to call refresh() would raise AttributeError AND bump the mock's
    # call count — either way the test would fail.
    cloud_mock = AsyncMock()
    app_state = SimpleNamespace(session_store=store, cloud_client=cloud_mock)
    request = _mock_request(cookies={SESSION_COOKIE: session.session_id}, app_state=app_state)

    # get_current_session should delete the session and return None.
    result = await get_current_session(request, store=store)
    assert result is None

    # Session was deleted.
    assert await store.get(session.session_id) is None

    # No refresh call made.
    assert not cloud_mock.refresh.called
    assert not cloud_mock.called

    # require_session turns the None into AuthRequired.
    with pytest.raises(AuthRequired):
        await require_session(session=None)


@pytest.mark.asyncio
async def test_no_cookie_returns_none(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    request = _mock_request(cookies={})
    assert await get_current_session(request, store=store) is None


@pytest.mark.asyncio
async def test_valid_session_returns_it_without_touching_cloud(
    fake_redis,  # type: ignore[no-untyped-def]
) -> None:
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u1",
        email="a@b.c",
        access_token="at-live",
        access_token_expires_in_seconds=3600,
        ip="1.1.1.1",
        user_agent="pytest",
    )
    request = _mock_request(cookies={SESSION_COOKIE: session.session_id})
    result = await get_current_session(request, store=store)
    assert result is not None
    assert result.session_id == session.session_id
    # No need for cloud or any refresh infrastructure — check that no
    # `refresh` attribute ever existed on the client class.
    from backend.clients import ndi_cloud as nc_mod
    assert not hasattr(nc_mod.NdiCloudClient, "refresh")
