"""FastAPI dependencies for auth.

- `get_current_session` — optional, returns None if unauthenticated.
- `require_session` — raises AuthRequired if no valid session.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..clients.ndi_cloud import NdiCloudClient
from ..errors import AuthRequired
from ..observability.logging import user_id_hash_ctx
from .session import SessionData, SessionStore
from .token_refresh import ensure_fresh_access_token

SESSION_COOKIE = "session"


def _get_session_store(request: Request) -> SessionStore:
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise RuntimeError("SessionStore not initialized on app.state")
    return store


def _get_cloud_client(request: Request) -> NdiCloudClient:
    client = getattr(request.app.state, "cloud_client", None)
    if client is None:
        raise RuntimeError("NdiCloudClient not initialized on app.state")
    return client


async def get_current_session(
    request: Request,
    store: Annotated[SessionStore, Depends(_get_session_store)],
    cloud: Annotated[NdiCloudClient, Depends(_get_cloud_client)],
) -> SessionData | None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None
    session = await store.get(session_id)
    if session is None:
        return None
    session = await ensure_fresh_access_token(session, store=store, cloud=cloud)
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
