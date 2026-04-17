"""Session fingerprint enforcement in ``get_current_session``.

PR-5 of Plan A: UA hash mismatch is a hard reject (revoke + AuthRequired).
IP hash mismatch is warn-only — mobile users legitimately roam.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog
from structlog.testing import capture_logs

from backend.auth.dependencies import SESSION_COOKIE, get_current_session
from backend.auth.session import SessionStore, fingerprint
from backend.errors import AuthRequired


@dataclass
class _FakeClient:
    host: str


class _FakeRequest:
    """Minimal stand-in for starlette.Request with just the bits our code reads."""

    def __init__(
        self,
        *,
        session_id: str | None,
        ip: str,
        user_agent: str,
    ) -> None:
        self.client = _FakeClient(host=ip) if ip else None
        self.headers: dict[str, str] = {"user-agent": user_agent}
        self.cookies: dict[str, str] = (
            {SESSION_COOKIE: session_id} if session_id else {}
        )


class _ReturnSelfCloud:
    """Stand-in for NdiCloudClient — never invoked because we bypass refresh."""


@pytest.fixture(autouse=True)
def _configure_structlog_for_capture() -> None:
    """capture_logs() only sees events when structlog is configured."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


@pytest.fixture
def long_token_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make access tokens look fresh so ``ensure_fresh_access_token`` is a no-op."""
    from backend.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ACCESS_TOKEN_REFRESH_GRACE_SECONDS", 0)


@pytest.mark.asyncio
async def test_fingerprint_helper_deterministic() -> None:
    """Same request produces same (ip_hash, ua_hash) every time."""
    req = _FakeRequest(session_id=None, ip="10.0.0.1", user_agent="curl/8")
    h1 = fingerprint(req)  # type: ignore[arg-type]
    h2 = fingerprint(req)  # type: ignore[arg-type]
    assert h1 == h2
    assert len(h1[0]) == 32  # truncated sha256 hex
    assert len(h1[1]) == 32

    # Different UA → different UA hash, same IP hash.
    other = _FakeRequest(session_id=None, ip="10.0.0.1", user_agent="curl/9")
    h3 = fingerprint(other)  # type: ignore[arg-type]
    assert h3[0] == h1[0]
    assert h3[1] != h1[1]


@pytest.mark.asyncio
async def test_fingerprint_matches_session_store_creation_hashes(fake_redis: Any) -> None:
    """Helper must produce identical hashes to the ones ``SessionStore.create`` uses.

    Drift here is the exact bug the helper exists to prevent: if login hashes
    one way and ``get_current_session`` hashes another, every request would
    trip the UA mismatch and re-login the world.
    """
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u",
        email="a@b.c",
        access_token="a",
        refresh_token=None,
        access_token_expires_in_seconds=3600,
        ip="1.2.3.4",
        user_agent="Mozilla/5.0",
    )
    req = _FakeRequest(session_id=None, ip="1.2.3.4", user_agent="Mozilla/5.0")
    ip_hash, ua_hash = fingerprint(req)  # type: ignore[arg-type]
    assert ip_hash == session.ip_addr_hash
    assert ua_hash == session.user_agent_hash


@pytest.mark.asyncio
async def test_matching_fingerprint_proceeds_normally(
    fake_redis: Any, long_token_ttl: None
) -> None:
    """IP1/UA1 in, IP1/UA1 at request time — passes through unchanged."""
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a", refresh_token=None,
        access_token_expires_in_seconds=3600, ip="10.0.0.1", user_agent="Firefox",
    )
    req = _FakeRequest(
        session_id=session.session_id, ip="10.0.0.1", user_agent="Firefox",
    )
    result = await get_current_session(
        request=req,  # type: ignore[arg-type]
        store=store,
        cloud=_ReturnSelfCloud(),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.session_id == session.session_id
    # Session still present in Redis.
    assert await store.get(session.session_id) is not None


@pytest.mark.asyncio
async def test_ua_mismatch_revokes_session_and_returns_auth_required(
    fake_redis: Any, long_token_ttl: None
) -> None:
    """UA1 in, UA2 at request — hijack defense. Reject + revoke."""
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a", refresh_token=None,
        access_token_expires_in_seconds=3600, ip="10.0.0.1", user_agent="Firefox",
    )
    attacker_req = _FakeRequest(
        session_id=session.session_id,
        ip="10.0.0.1",
        user_agent="curl/8.0",  # Different UA — someone stole the cookie.
    )
    with pytest.raises(AuthRequired):
        await get_current_session(
            request=attacker_req,  # type: ignore[arg-type]
            store=store,
            cloud=_ReturnSelfCloud(),  # type: ignore[arg-type]
        )
    # Session revoked — the stolen cookie is now useless.
    assert await store.get(session.session_id) is None


@pytest.mark.asyncio
async def test_ip_change_logs_warning_allows_request(
    fake_redis: Any, long_token_ttl: None
) -> None:
    """IP1 in, IP2 at request time (same UA) — warn but proceed.

    Mobile users legitimately roam between wifi/cell. Hard-rejecting on IP
    would force a re-login every time someone walks out of a coffee shop.
    """
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a", refresh_token=None,
        access_token_expires_in_seconds=3600, ip="192.168.1.10", user_agent="Firefox",
    )
    roaming_req = _FakeRequest(
        session_id=session.session_id,
        ip="10.0.0.5",  # Different IP — wifi → cell roam.
        user_agent="Firefox",
    )
    with capture_logs() as logs:
        result = await get_current_session(
            request=roaming_req,  # type: ignore[arg-type]
            store=store,
            cloud=_ReturnSelfCloud(),  # type: ignore[arg-type]
        )
    assert result is not None  # Request proceeded.
    # Structured warning was emitted with hashes (no raw IPs).
    ip_events = [e for e in logs if e.get("event") == "session.ip_changed"]
    assert len(ip_events) == 1
    e = ip_events[0]
    assert e["session_id"] == session.session_id
    assert e["stored_ip_hash"] == session.ip_addr_hash
    assert e["current_ip_hash"] != session.ip_addr_hash
    # No raw IPs in the log line.
    payload = str(e)
    assert "192.168.1.10" not in payload
    assert "10.0.0.5" not in payload


@pytest.mark.asyncio
async def test_ua_mismatch_does_not_proceed_to_touch(
    fake_redis: Any, long_token_ttl: None
) -> None:
    """UA mismatch must short-circuit before ``store.touch`` — no last_active update."""
    store = SessionStore(fake_redis)
    # Patch touch so if it gets called we know the short-circuit failed.
    store.touch = AsyncMock()  # type: ignore[method-assign]
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a", refresh_token=None,
        access_token_expires_in_seconds=3600, ip="1.1.1.1", user_agent="Firefox",
    )
    attacker_req = _FakeRequest(
        session_id=session.session_id, ip="1.1.1.1", user_agent="Other",
    )
    with pytest.raises(AuthRequired):
        await get_current_session(
            request=attacker_req,  # type: ignore[arg-type]
            store=store,
            cloud=_ReturnSelfCloud(),  # type: ignore[arg-type]
        )
    store.touch.assert_not_called()
