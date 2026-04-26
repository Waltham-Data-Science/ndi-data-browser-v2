"""Idle session timeout enforcement (O3).

The audit (synthesis §O3) flagged that ``Settings.SESSION_IDLE_TTL_SECONDS``
was defined but never consulted. The Redis TTL was set to the absolute
lifetime (24h), so a session that hadn't seen activity in 23h was still
valid as long as the absolute clock hadn't elapsed. That's the
opposite of what an idle TTL is supposed to do.

This change closes the gap two ways (belt-and-suspenders):

1. The Redis TTL is refreshed on every ``touch`` to
   ``min(remaining_absolute, idle_ttl)``. An idle session expires from
   Redis naturally after ``idle_ttl`` of no activity.
2. ``get_current_session`` does an explicit `now - last_active >
   idle_ttl` check before returning the session — covers edge cases
   where the Redis TTL refresh raced ahead of the actual activity, and
   makes the behavior testable in unit-test time without touching
   Redis-server clocks.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog

from backend.auth.dependencies import SESSION_COOKIE, get_current_session
from backend.auth.session import SessionData, SessionStore
from backend.config import get_settings


@pytest.fixture(autouse=True)
def _configure_structlog_for_capture() -> None:
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


def _make_session(*, last_active_offset_seconds: int) -> SessionData:
    """Build a session whose ``last_active`` is N seconds in the past."""
    now = int(time.time())
    return SessionData(
        session_id="abc123def4567890",
        user_id="u1",
        user_email_hash="emailhash" + "0" * 24,
        access_token="opaque-token",
        access_token_expires_at=now + 3600,
        issued_at=now - 10,  # well under the absolute window
        last_active=now - last_active_offset_seconds,
        ip_addr_hash="0" * 32,
        user_agent_hash="0" * 32,
        organization_ids=[],
        is_admin=False,
    )


def _make_request(*, session_id: str = "abc123def4567890") -> SimpleNamespace:
    """Mock request with cookie + the headers fingerprint() reads."""
    cookies = {SESSION_COOKIE: session_id}
    return SimpleNamespace(
        cookies=cookies,
        client=SimpleNamespace(host="unknown"),
        headers={"user-agent": "unknown"},
        app=SimpleNamespace(state=SimpleNamespace()),
    )


@pytest.mark.asyncio
async def test_get_current_session_rejects_session_idle_past_ttl(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A session whose ``last_active`` is older than SESSION_IDLE_TTL_SECONDS
    must be deleted and surface as None (which require_session turns into
    AuthRequired). The Redis TTL alone isn't sufficient — pyramid the
    explicit check so the behavior is observable in unit-test time."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "60")  # 1-minute idle
    get_settings.cache_clear()
    try:
        store = AsyncMock(spec=SessionStore)
        # 5 minutes of inactivity > 60s idle window.
        stale_session = _make_session(last_active_offset_seconds=300)
        store.get.return_value = stale_session
        store.delete = AsyncMock()
        store.touch = AsyncMock()

        request = _make_request()
        # Wire the store onto request.app.state — same path the real
        # `_get_session_store` uses.
        request.app.state.session_store = store
        # Also need the fingerprint hashes on the session to match
        # request — populate them from the request's IP/UA so the UA-
        # check doesn't trip first.
        from backend.auth.session import _hash_ip, _hash_user_agent
        stale_session.ip_addr_hash = _hash_ip("unknown")
        stale_session.user_agent_hash = _hash_user_agent("unknown")

        result = await get_current_session(request, store)  # type: ignore[arg-type]
        assert result is None
        store.delete.assert_called_once_with(stale_session.session_id)
        # Idle session was rejected BEFORE touch — no last_active update
        # on a session we just decided to kill.
        store.touch.assert_not_called()
    finally:
        monkeypatch.delenv("SESSION_IDLE_TTL_SECONDS", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_get_current_session_accepts_recently_active_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An active session (last_active < idle TTL ago) is allowed through
    and `touch`'d. Symmetric with the rejection test."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "60")
    get_settings.cache_clear()
    try:
        store = AsyncMock(spec=SessionStore)
        # 30s of inactivity < 60s idle window.
        fresh_session = _make_session(last_active_offset_seconds=30)
        from backend.auth.session import _hash_ip, _hash_user_agent
        fresh_session.ip_addr_hash = _hash_ip("unknown")
        fresh_session.user_agent_hash = _hash_user_agent("unknown")
        store.get.return_value = fresh_session
        store.touch = AsyncMock()

        request = _make_request()
        request.app.state.session_store = store

        result = await get_current_session(request, store)  # type: ignore[arg-type]
        assert result is fresh_session
        store.touch.assert_called_once_with(fresh_session)
    finally:
        monkeypatch.delenv("SESSION_IDLE_TTL_SECONDS", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_session_store_write_uses_idle_ttl_for_redis_expiry(fake_redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Redis TTL on write is `min(remaining_absolute, idle_ttl)`. With a
    1-hour idle and a 24-hour absolute, Redis should set a 1-hour TTL on a
    fresh session — so an idle session naturally expires from Redis
    without us having to ping it."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "3600")
    monkeypatch.setenv("SESSION_ABSOLUTE_TTL_SECONDS", "86400")
    get_settings.cache_clear()
    try:
        store = SessionStore(fake_redis)
        session = await store.create(
            user_id="u1",
            email="u1@example.test",
            access_token="tok",
            access_token_expires_in_seconds=3600,
            ip="1.2.3.4",
            user_agent="ua",
        )
        ttl = await fake_redis.ttl(f"session:{session.session_id}")
        # Idle TTL is 3600s. The remaining absolute is ~86400s. min = 3600.
        # Allow a few seconds of slop for the Redis ttl call latency.
        assert 3550 <= ttl <= 3600, f"expected ~3600s idle TTL, got {ttl}"
    finally:
        monkeypatch.delenv("SESSION_IDLE_TTL_SECONDS", raising=False)
        monkeypatch.delenv("SESSION_ABSOLUTE_TTL_SECONDS", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_session_store_write_caps_at_remaining_absolute_when_smaller(fake_redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When remaining absolute < idle, Redis TTL caps at remaining absolute.
    Otherwise a session in its 23rd hour would get a fresh 2-hour idle
    extension, letting it live past its 24-hour ceiling."""
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "7200")  # 2h idle
    monkeypatch.setenv("SESSION_ABSOLUTE_TTL_SECONDS", "60")  # 1-min absolute
    get_settings.cache_clear()
    try:
        store = SessionStore(fake_redis)
        session = await store.create(
            user_id="u2",
            email="u2@example.test",
            access_token="tok",
            access_token_expires_in_seconds=3600,
            ip="1.2.3.4",
            user_agent="ua",
        )
        ttl = await fake_redis.ttl(f"session:{session.session_id}")
        # min(remaining_absolute=60, idle=7200) = 60.
        assert 50 <= ttl <= 60, f"expected ~60s remaining-absolute TTL, got {ttl}"
    finally:
        monkeypatch.delenv("SESSION_IDLE_TTL_SECONDS", raising=False)
        monkeypatch.delenv("SESSION_ABSOLUTE_TTL_SECONDS", raising=False)
        get_settings.cache_clear()
