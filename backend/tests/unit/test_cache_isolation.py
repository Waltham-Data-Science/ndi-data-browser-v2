"""PR-3 regressions: per-user cache-key isolation.

Prevents two authenticated users from sharing a cached entry after the
cloud ships any per-user variation. Currently safe by construction (cloud
returns user-invariant bodies for all cached endpoints) but would become
a critical data-leak bug the moment that invariant changed.

Schema bump ``v3 → v4`` means in-flight v3 entries are naturally ignored
on deploy; they TTL out within 1 hour.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest

from backend.auth.session import SessionData, user_scope_for
from backend.cache.redis_table import RedisTableCache


def _make_session(user_id: str) -> SessionData:
    """Minimal SessionData for tests that only read user_id."""
    return SessionData(
        session_id=f"sid-{user_id}",
        user_id=user_id,
        user_email_hash=hashlib.sha256(user_id.encode()).hexdigest(),
        access_token="access",
        refresh_token=None,
        access_token_expires_at=int(time.time()) + 3600,
        issued_at=int(time.time()),
        last_active=int(time.time()),
        ip_addr_hash="ip",
        user_agent_hash="ua",
    )


# ---------------------------------------------------------------------------
# user_scope_for helper
# ---------------------------------------------------------------------------

class TestUserScopeForHelper:
    def test_none_session_returns_public(self) -> None:
        assert user_scope_for(None) == "public"

    def test_user_scope_for_helper_is_stable(self) -> None:
        """Same user_id → same scope string (determinism required so key
        structure is predictable across processes and deploys). Different
        user_ids → different scope strings (the whole point of PR-3)."""
        s1 = _make_session("alice@example.com")
        s2 = _make_session("alice@example.com")
        s3 = _make_session("bob@example.com")
        assert user_scope_for(s1) == user_scope_for(s2)
        assert user_scope_for(s1) != user_scope_for(s3)

    def test_scope_string_is_short_and_prefixed(self) -> None:
        """``u:`` + 16 hex chars = 18 total. Bounded so Redis memory
        doesn't blow up per-user and keys stay readable in debugging."""
        s = _make_session("alice@example.com")
        scope = user_scope_for(s)
        assert scope.startswith("u:")
        # Strip prefix; remainder is hex digest suffix.
        suffix = scope[2:]
        assert len(suffix) == 16
        # Hex-only.
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_public_has_no_u_prefix(self) -> None:
        """The ``u:`` prefix is the namespace marker — the public bucket
        must not look like a per-user bucket."""
        assert not user_scope_for(None).startswith("u:")


# ---------------------------------------------------------------------------
# Redis-key construction
# ---------------------------------------------------------------------------

class TestCacheKeyConstructionIncludesUserScope:
    def test_authed_key_contains_user_hash(self) -> None:
        s = _make_session("alice@example.com")
        scope = user_scope_for(s)
        key = RedisTableCache.table_key("DS1", "subject", user_scope=scope)
        assert key == f"table:v4:DS1:subject:{scope}"
        assert "u:" in key

    def test_public_key_uses_public_literal(self) -> None:
        key = RedisTableCache.table_key(
            "DS1", "subject", user_scope=user_scope_for(None),
        )
        assert key.endswith(":public")
        assert "u:" not in key


# ---------------------------------------------------------------------------
# Cross-user isolation under the Redis cache
# ---------------------------------------------------------------------------

async def test_authed_cache_isolated_by_user(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """Two sessions with different user_id values MUST write to different
    Redis keys and MUST NOT read the other's cached entry.
    """
    cache = RedisTableCache(fake_redis, ttl_seconds=60)
    alice = _make_session("alice@example.com")
    bob = _make_session("bob@example.com")

    k_alice = RedisTableCache.table_key("DS1", "subject", user_scope=user_scope_for(alice))
    k_bob = RedisTableCache.table_key("DS1", "subject", user_scope=user_scope_for(bob))
    assert k_alice != k_bob, "Different users must produce different keys"

    alice_calls = 0
    bob_calls = 0

    async def build_alice() -> dict:
        nonlocal alice_calls
        alice_calls += 1
        return {"rows": [{"who": "alice"}]}

    async def build_bob() -> dict:
        nonlocal bob_calls
        bob_calls += 1
        return {"rows": [{"who": "bob"}]}

    # Alice populates her bucket.
    v_alice = await cache.get_or_compute(k_alice, build_alice)
    assert v_alice == {"rows": [{"who": "alice"}]}
    assert alice_calls == 1

    # Bob MUST NOT see alice's data — his get_or_compute runs his own
    # producer (proving he missed cache) and gets his own payload.
    v_bob = await cache.get_or_compute(k_bob, build_bob)
    assert v_bob == {"rows": [{"who": "bob"}]}
    assert bob_calls == 1

    # And both keys coexist in Redis with their own payloads.
    raw_alice = await fake_redis.get(k_alice)
    raw_bob = await fake_redis.get(k_bob)
    assert json.loads(raw_alice) == {"rows": [{"who": "alice"}]}
    assert json.loads(raw_bob) == {"rows": [{"who": "bob"}]}


async def test_public_cache_shared_across_anonymous_requests(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """Two unauthenticated requests MUST hit the same ``public``-scoped
    entry — unchanged behavior from v3. This is the whole point of having
    a public bucket: cache amortizes across all anonymous browsers.
    """
    cache = RedisTableCache(fake_redis, ttl_seconds=60)
    scope = user_scope_for(None)  # "public"
    key = RedisTableCache.table_key("DS1", "subject", user_scope=scope)

    calls = 0

    async def build() -> dict:
        nonlocal calls
        calls += 1
        return {"rows": [{"public": True}]}

    v1 = await cache.get_or_compute(key, build)
    v2 = await cache.get_or_compute(key, build)
    assert v1 == v2 == {"rows": [{"public": True}]}
    assert calls == 1, "Second anonymous request must be served from cache"


async def test_schema_v4_does_not_read_v3_entries(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """A pre-PR-3 v3 entry must NOT be readable via the new v4 code path.

    This is what the SCHEMA_VERSION bump is for: on deploy, any in-flight
    v3 blobs in Redis are ignored (their keys don't match v4 keys) and TTL
    out within one hour. We model this by writing a v3-shaped key and
    proving the new v4 cache path treats it as a miss and runs the producer.
    """
    cache = RedisTableCache(fake_redis, ttl_seconds=60)

    # Write a v3-shaped entry (old key + old payload). This simulates a
    # pre-deploy entry that may still be in Redis during rollout.
    v3_key = "table:v3:DS1:subject:authed"
    await fake_redis.set(v3_key, json.dumps({"rows": [{"stale": True}]}))

    # A v4 caller with an authed session would construct a DIFFERENT key
    # and therefore miss the v3 entry — this is the point. We run a
    # real v4 get_or_compute and confirm the producer ran (i.e. we did NOT
    # read the v3 payload).
    alice = _make_session("alice@example.com")
    v4_key = RedisTableCache.table_key(
        "DS1", "subject", user_scope=user_scope_for(alice),
    )
    assert v4_key != v3_key, "v4 key must differ from v3 key"
    assert v4_key.startswith("table:v4:"), "v4 key must carry v4 version prefix"

    calls = 0

    async def build() -> dict:
        nonlocal calls
        calls += 1
        return {"rows": [{"fresh": True}]}

    v = await cache.get_or_compute(v4_key, build)
    assert v == {"rows": [{"fresh": True}]}
    assert calls == 1, "v4 read must MISS the v3 entry and run the producer"

    # And the v3 entry remains untouched (will TTL out naturally on its own).
    assert await fake_redis.get(v3_key) == json.dumps({"rows": [{"stale": True}]})


# ---------------------------------------------------------------------------
# Static guard against accidental regression
# ---------------------------------------------------------------------------

def test_table_key_rejects_legacy_authed_kwarg() -> None:
    """The old ``authed: bool`` dimension is gone; callers passing it
    should fail loudly rather than silently false-share under a ``True``
    literal that would collide across all authenticated users.
    """
    with pytest.raises(TypeError):
        RedisTableCache.table_key("DS1", "subject", authed=True)  # type: ignore[call-arg]
