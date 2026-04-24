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


# --- Audit 2026-04-23 (#56): corrupt-payload resilience ---------------------
#
# Prior to the fix, any schema drift in a Redis session blob (missing field,
# wrong type, etc.) propagated ``KeyError``/``TypeError``/``ValueError`` out
# of ``SessionStore.get`` — surfacing as a 500 to the user on every read.
# Post-fix the corrupt blob is soft-deleted and the caller sees ``None``
# (re-login). These tests pin that behavior.

@pytest.mark.asyncio
async def test_get_returns_none_on_missing_required_field(fake_redis) -> None:  # type: ignore[no-untyped-def]
    import json
    store = SessionStore(fake_redis)
    sid = "corrupt123"
    await fake_redis.set(
        f"session:{sid}",
        json.dumps({
            "session_id": sid,
            # user_id intentionally absent
            "user_email_hash": "x" * 64,
            "access_token": store.fernet.encrypt(b"secret").decode(),
            "access_token_expires_at": 2000000000,
            "issued_at": 1000,
            "last_active": 1000,
            "ip_addr_hash": "i" * 32,
            "user_agent_hash": "u" * 32,
        }),
    )
    # Must not raise — caller gets None and the bad blob is removed.
    assert await store.get(sid) is None
    assert await fake_redis.get(f"session:{sid}") is None


@pytest.mark.asyncio
async def test_get_returns_none_on_wrong_type(fake_redis) -> None:  # type: ignore[no-untyped-def]
    import json
    store = SessionStore(fake_redis)
    sid = "badtype"
    await fake_redis.set(
        f"session:{sid}",
        json.dumps({
            "session_id": sid,
            "user_id": "u",
            "user_email_hash": "x" * 64,
            "access_token": store.fernet.encrypt(b"s").decode(),
            # String where int is required — should soft-delete, not crash.
            "access_token_expires_at": "not-a-number",
            "issued_at": 1000,
            "last_active": 1000,
            "ip_addr_hash": "i" * 32,
            "user_agent_hash": "u" * 32,
        }),
    )
    assert await store.get(sid) is None
    assert await fake_redis.get(f"session:{sid}") is None


@pytest.mark.asyncio
async def test_get_returns_none_on_malformed_json(fake_redis) -> None:  # type: ignore[no-untyped-def]
    store = SessionStore(fake_redis)
    sid = "badjson"
    await fake_redis.set(f"session:{sid}", "{not valid json at all")
    assert await store.get(sid) is None
    # Bad JSON should also be cleaned up so it can't re-crash.
    assert await fake_redis.get(f"session:{sid}") is None


@pytest.mark.asyncio
async def test_get_returns_none_on_invalid_fernet_token(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """Fernet decryption failure (e.g. encryption-key rotation) surfaces
    as a soft re-auth, not a 500."""
    import json
    store = SessionStore(fake_redis)
    sid = "badcrypt"
    await fake_redis.set(
        f"session:{sid}",
        json.dumps({
            "session_id": sid,
            "user_id": "u",
            "user_email_hash": "x" * 64,
            "access_token": "not-a-real-fernet-token",
            "access_token_expires_at": 2000000000,
            "issued_at": 1000,
            "last_active": 1000,
            "ip_addr_hash": "i" * 32,
            "user_agent_hash": "u" * 32,
        }),
    )
    assert await store.get(sid) is None
    assert await fake_redis.get(f"session:{sid}") is None
