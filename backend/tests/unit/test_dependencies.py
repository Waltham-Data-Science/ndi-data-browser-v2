"""Auth dependency unit tests.

Covers two behaviors of ``get_current_session`` / ``require_session``:

- Session fingerprint enforcement (PR-5): UA hash mismatch is a hard reject
  (revoke + AuthRequired). IP hash mismatch is warn-only — mobile users
  legitimately roam.
- Access-token expiry (ADR-008): the cloud does not expose a refresh endpoint,
  so an expired token drops the session and surfaces as AuthRequired with
  zero attempts to call any ``cloud.refresh`` path.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog
from structlog.testing import capture_logs

from backend.auth.dependencies import (
    SESSION_COOKIE,
    get_current_session,
    require_session,
)
from backend.auth.session import SessionStore, fingerprint
from backend.errors import AuthRequired


@dataclass
class _FakeClient:
    host: str


class _FakeRequest:
    """Minimal stand-in for starlette.Request with the bits fingerprint() reads."""

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


def _mock_request(*, cookies: dict[str, str] | None = None, app_state: object | None = None):
    """Lighter stand-in for tests that don't exercise fingerprint/IP paths.

    Used for expiry-only tests where fingerprint matching isn't under test.
    Provides empty client/headers so fingerprint() still returns a stable pair.
    """
    cookies = cookies or {}
    state = app_state or SimpleNamespace()
    app = SimpleNamespace(state=state)
    client = SimpleNamespace(host="unknown")
    headers: dict[str, str] = {}
    return SimpleNamespace(cookies=cookies, app=app, client=client, headers=headers)


@pytest.fixture(autouse=True)
def _configure_structlog_for_capture() -> None:
    """capture_logs() only sees events when structlog is configured."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


# ---------------------------------------------------------------------------
# Fingerprint helper (PR-5)
# ---------------------------------------------------------------------------


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
        access_token_expires_in_seconds=3600,
        ip="1.2.3.4",
        user_agent="Mozilla/5.0",
    )
    req = _FakeRequest(session_id=None, ip="1.2.3.4", user_agent="Mozilla/5.0")
    ip_hash, ua_hash = fingerprint(req)  # type: ignore[arg-type]
    assert ip_hash == session.ip_addr_hash
    assert ua_hash == session.user_agent_hash


# ---------------------------------------------------------------------------
# Fingerprint enforcement in get_current_session (PR-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matching_fingerprint_proceeds_normally(fake_redis: Any) -> None:
    """IP1/UA1 in, IP1/UA1 at request time — passes through unchanged."""
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a",
        access_token_expires_in_seconds=3600, ip="10.0.0.1", user_agent="Firefox",
    )
    req = _FakeRequest(
        session_id=session.session_id, ip="10.0.0.1", user_agent="Firefox",
    )
    result = await get_current_session(
        request=req,  # type: ignore[arg-type]
        store=store,
    )
    assert result is not None
    assert result.session_id == session.session_id
    # Session still present in Redis.
    assert await store.get(session.session_id) is not None


@pytest.mark.asyncio
async def test_ua_mismatch_revokes_session_and_returns_auth_required(
    fake_redis: Any,
) -> None:
    """UA1 in, UA2 at request — hijack defense. Reject + revoke."""
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a",
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
        )
    # Session revoked — the stolen cookie is now useless.
    assert await store.get(session.session_id) is None


@pytest.mark.asyncio
async def test_ip_change_logs_warning_allows_request(fake_redis: Any) -> None:
    """IP1 in, IP2 at request time (same UA) — warn but proceed.

    Mobile users legitimately roam between wifi/cell. Hard-rejecting on IP
    would force a re-login every time someone walks out of a coffee shop.
    """
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a",
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
        )
    assert result is not None  # Request proceeded.
    # Structured warning was emitted with hashes (no raw IPs).
    ip_events = [e for e in logs if e.get("event") == "session.ip_changed"]
    assert len(ip_events) == 1
    e = ip_events[0]
    # session_id is truncated to 8 chars (the full id is the session
    # secret — see dependencies.py for the rationale). The prefix is
    # still enough to correlate log lines for one session.
    assert e["session_id"] == session.session_id[:8]
    assert e["stored_ip_hash"] == session.ip_addr_hash
    assert e["current_ip_hash"] != session.ip_addr_hash
    # No raw IPs in the log line.
    payload = str(e)
    assert "192.168.1.10" not in payload
    assert "10.0.0.5" not in payload
    # And the full session id MUST NOT appear anywhere in the
    # captured event payload — otherwise log-readers could replay it.
    assert session.session_id not in payload


@pytest.mark.asyncio
async def test_ua_mismatch_does_not_proceed_to_touch(fake_redis: Any) -> None:
    """UA mismatch must short-circuit before ``store.touch`` — no last_active update."""
    store = SessionStore(fake_redis)
    # Patch touch so if it gets called we know the short-circuit failed.
    store.touch = AsyncMock()  # type: ignore[method-assign]
    session = await store.create(
        user_id="u", email="a@b.c", access_token="a",
        access_token_expires_in_seconds=3600, ip="1.1.1.1", user_agent="Firefox",
    )
    attacker_req = _FakeRequest(
        session_id=session.session_id, ip="1.1.1.1", user_agent="Other",
    )
    with pytest.raises(AuthRequired):
        await get_current_session(
            request=attacker_req,  # type: ignore[arg-type]
            store=store,
        )
    store.touch.assert_not_called()


# ---------------------------------------------------------------------------
# Access-token expiry path (ADR-008)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_access_token_raises_auth_required_no_refresh_attempt(
    fake_redis: Any,
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

    # A mock cloud is attached to app.state just so that any accidental
    # attempt to call refresh() would raise AttributeError AND bump the mock's
    # call count — either way the test would fail.
    cloud_mock = AsyncMock()
    app_state = SimpleNamespace(session_store=store, cloud_client=cloud_mock)
    # Match the session's IP/UA so the fingerprint check passes and we exercise
    # the expiry branch specifically.
    request = _FakeRequest(
        session_id=session.session_id, ip="1.1.1.1", user_agent="pytest",
    )
    request.app = SimpleNamespace(state=app_state)  # type: ignore[attr-defined]

    # get_current_session should delete the session and return None.
    result = await get_current_session(request, store=store)  # type: ignore[arg-type]
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
async def test_no_cookie_returns_none(fake_redis: Any) -> None:
    store = SessionStore(fake_redis)
    request = _mock_request(cookies={})
    assert await get_current_session(request, store=store) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_valid_session_returns_it_without_touching_cloud(
    fake_redis: Any,
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
    # Match fingerprint to exercise the happy path.
    request = _FakeRequest(
        session_id=session.session_id, ip="1.1.1.1", user_agent="pytest",
    )
    result = await get_current_session(request, store=store)  # type: ignore[arg-type]
    assert result is not None
    assert result.session_id == session.session_id
    # No need for cloud or any refresh infrastructure — check that no
    # `refresh` attribute ever existed on the client class.
    from backend.clients import ndi_cloud as nc_mod
    assert not hasattr(nc_mod.NdiCloudClient, "refresh")
