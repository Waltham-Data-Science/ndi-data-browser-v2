"""Session store round-trips encrypted tokens through Redis."""
from __future__ import annotations

import pytest

from backend.auth.session import SessionStore


@pytest.mark.asyncio
async def test_create_and_get_session(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u1",
        email="alice@example.com",
        access_token="at-secret",
        refresh_token="rt-secret",
        access_token_expires_in_seconds=3600,
        ip="127.0.0.1",
        user_agent="pytest",
    )
    assert session.session_id
    fetched = await store.get(session.session_id)
    assert fetched is not None
    assert fetched.access_token == "at-secret"
    assert fetched.refresh_token == "rt-secret"
    assert fetched.user_email_hash == session.user_email_hash
    # Never stored plaintext — verify by looking at the raw Redis value.
    raw = await fake_redis.get(f"session:{session.session_id}")
    assert "at-secret" not in raw
    assert "rt-secret" not in raw
    assert "alice@example.com" not in raw


@pytest.mark.asyncio
async def test_delete_session(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a", refresh_token="r",
        access_token_expires_in_seconds=60, ip="1.2.3.4", user_agent="x",
    )
    await store.delete(session.session_id)
    assert await store.get(session.session_id) is None


@pytest.mark.asyncio
async def test_refresh_lock_is_mutually_exclusive(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    sid = "deadbeef"
    assert await store.acquire_refresh_lock(sid)
    assert not await store.acquire_refresh_lock(sid)
    await store.release_refresh_lock(sid)
    assert await store.acquire_refresh_lock(sid)


@pytest.mark.asyncio
async def test_update_tokens(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    s = await store.create(
        user_id="u", email="a@b.c", access_token="old", refresh_token="rt",
        access_token_expires_in_seconds=60, ip="1", user_agent="x",
    )
    await store.update_tokens(s, access_token="new", refresh_token="rt2", access_token_expires_in_seconds=120)
    fresh = await store.get(s.session_id)
    assert fresh is not None
    assert fresh.access_token == "new"
    assert fresh.refresh_token == "rt2"
