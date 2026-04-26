"""FastAPI dependencies for auth.

- `get_current_session` — optional, returns None if unauthenticated.
- `require_session` — raises AuthRequired if no valid session.
"""
from __future__ import annotations

import time
from typing import Annotated, cast

from fastapi import Depends, Request

from ..config import get_settings
from ..errors import AuthRequired
from ..observability.logging import get_logger, user_id_hash_ctx
from .session import SessionData, SessionStore, fingerprint

SESSION_COOKIE = "session"

log = get_logger(__name__)


def _get_session_store(request: Request) -> SessionStore:
    # getattr on Starlette's State returns Any; narrow to the concrete type.
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise RuntimeError("SessionStore not initialized on app.state")
    return cast(SessionStore, store)


async def get_current_session(
    request: Request,
    store: Annotated[SessionStore, Depends(_get_session_store)],
) -> SessionData | None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None
    session = await store.get(session_id)
    if session is None:
        return None

    # Device-binding enforcement. UA hash mismatch is a hard reject (revoke +
    # re-login). IP hash mismatch is warn-only — mobile users legitimately
    # roam across networks, and hard-rejecting would shred UX.
    current_ip_hash, current_ua_hash = fingerprint(request)
    if current_ua_hash != session.user_agent_hash:
        log.warning(
            "session.ua_changed",
            session_id=session.session_id,
            stored_ua_hash=session.user_agent_hash,
            current_ua_hash=current_ua_hash,
        )
        await store.delete(session.session_id)
        raise AuthRequired()
    if current_ip_hash != session.ip_addr_hash:
        log.warning(
            "session.ip_changed",
            session_id=session.session_id,
            stored_ip_hash=session.ip_addr_hash,
            current_ip_hash=current_ip_hash,
        )

    now = int(time.time())

    # Access tokens are 1-hour TTL and the cloud does not expose a refresh
    # endpoint (see ADR-008). If the token has already expired, drop the
    # session and force re-login via the standard AuthRequired path.
    if session.access_token_expires_at <= now:
        await store.delete(session.session_id)
        return None

    # O3: idle-timeout enforcement. Belt-and-suspenders alongside the
    # Redis-level TTL cap in `SessionStore._write` (which sets the key to
    # min(remaining_absolute, idle_ttl) on every touch). The explicit
    # check here covers the edge where the Redis TTL refresh raced ahead
    # of actual activity and makes the behavior testable in unit-test
    # time without touching Redis-server clocks. Surfaces as a normal
    # logged-out state — caller's `require_session` turns it into 401.
    settings = get_settings()
    idle_seconds = now - session.last_active
    if idle_seconds > settings.SESSION_IDLE_TTL_SECONDS:
        log.info(
            "session.idle_timeout",
            session_id=session.session_id[:8],
            idle_seconds=idle_seconds,
            limit=settings.SESSION_IDLE_TTL_SECONDS,
        )
        await store.delete(session.session_id)
        return None

    # Touch updates last_active without re-issuing an absolute TTL extension.
    await store.touch(session)
    user_id_hash_ctx.set(session.user_email_hash[:16])
    return session


async def require_session(
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> SessionData:
    if session is None:
        raise AuthRequired()
    return session
