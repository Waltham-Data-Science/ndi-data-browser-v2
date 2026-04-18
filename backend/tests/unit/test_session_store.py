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
        access_token_expires_in_seconds=3600,
        ip="127.0.0.1",
        user_agent="pytest",
    )
    assert session.session_id
    fetched = await store.get(session.session_id)
    assert fetched is not None
    assert fetched.access_token == "at-secret"
    assert fetched.user_email_hash == session.user_email_hash
    # Never stored plaintext — verify by looking at the raw Redis value.
    raw = await fake_redis.get(f"session:{session.session_id}")
    assert "at-secret" not in raw
    assert "alice@example.com" not in raw


@pytest.mark.asyncio
async def test_delete_session(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a",
        access_token_expires_in_seconds=60, ip="1.2.3.4", user_agent="x",
    )
    await store.delete(session.session_id)
    assert await store.get(session.session_id) is None


def test_token_refresh_module_is_gone() -> None:
    """Regression guard for ADR-008: no module should resurrect refresh scaffolding."""
    import importlib
    with pytest.raises(ImportError):
        importlib.import_module("backend.auth.token_refresh")
