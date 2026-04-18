"""FastAPI dependencies for auth.

- `get_current_session` — optional, returns None if unauthenticated.
- `require_session` — raises AuthRequired if no valid session.
"""
from __future__ import annotations

import time
from typing import Annotated, cast

from fastapi import Depends, Request

from ..errors import AuthRequired
from ..observability.logging import user_id_hash_ctx
from .session import SessionData, SessionStore

SESSION_COOKIE = "session"


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
    # Access tokens are 1-hour TTL and the cloud does not expose a refresh
    # endpoint (see ADR-008). If the token has already expired, drop the
    # session and force re-login via the standard AuthRequired path.
    if session.access_token_expires_at <= int(time.time()):
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
