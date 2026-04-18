"""Subject-resolution tests for the rate-limit dependency (PR-2 hardening).

Covers the fix for the cookie-rotation bypass: `_subject` must resolve the
session in Redis before using its value, otherwise an attacker rotates the
cookie to defeat per-user rate limits (each fake cookie gets a fresh bucket).
"""
from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from backend.auth.session import SessionStore
from backend.routers._deps import _subject


def _make_request(cookie_value: str | None, client_host: str = "1.2.3.4") -> Request:
    """Build a minimal Starlette Request with optional session cookie."""
    headers: list[tuple[bytes, bytes]] = []
    if cookie_value is not None:
        headers.append((b"cookie", f"session={cookie_value}".encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/datasets",
        "raw_path": b"/api/datasets",
        "query_string": b"",
        "headers": headers,
        "server": ("testserver", 80),
        "client": (client_host, 50000),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_subject_does_not_trust_unvalidated_cookie(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """A forged `session` cookie must NOT become the rate-limit subject.

    Previously `_subject` returned `f"u:{cookie_value}"` without verification,
    letting an attacker defeat per-user limits by rotating the cookie.
    """
    store = SessionStore(fake_redis)
    request = _make_request(cookie_value="fakefakefake", client_host="203.0.113.5")

    subject = await _subject(request, store)

    # Must NOT be bucketed by the fake cookie value.
    assert subject != "u:fakefakefake"
    assert "fakefakefake" not in subject
    # Must fall back to the IP-hashed subject.
    assert subject.startswith("i:")


@pytest.mark.asyncio
async def test_subject_uses_user_id_when_session_valid(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """A real session in Redis → bucket keyed by `u:<user_id>`."""
    store = SessionStore(fake_redis)
    session = await store.create(
        user_id="alice-123",
        email="alice@example.com",
        access_token="at",
        refresh_token="rt",
        access_token_expires_in_seconds=3600,
        ip="127.0.0.1",
        user_agent="pytest",
    )

    request = _make_request(cookie_value=session.session_id, client_host="203.0.113.9")

    subject = await _subject(request, store)

    assert subject == "u:alice-123"


@pytest.mark.asyncio
async def test_subject_falls_back_to_ip_when_no_cookie(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """No cookie at all → hashed-IP subject."""
    store = SessionStore(fake_redis)
    request = _make_request(cookie_value=None, client_host="198.51.100.1")

    subject = await _subject(request, store)

    assert subject.startswith("i:")
    # Raw IP must never appear in the bucket key.
    assert "198.51.100.1" not in subject


@pytest.mark.asyncio
async def test_subject_rotating_cookies_collapse_to_one_ip_bucket(fake_redis) -> None:  # type: ignore[no-untyped-def]
    """1000 fabricated cookies from one IP → one IP-hashed subject, not 1000.

    This is the attacker model: rotate the cookie rapidly to grow Redis keys.
    With the fix, every unvalidated cookie degrades to the same IP bucket.
    """
    store = SessionStore(fake_redis)
    subjects: set[str] = set()
    for i in range(1000):
        request = _make_request(cookie_value=f"forged-{i}", client_host="192.0.2.7")
        subjects.add(await _subject(request, store))

    # All 1000 requests collapse to a single IP-hashed subject.
    assert len(subjects) == 1
    assert next(iter(subjects)).startswith("i:")
