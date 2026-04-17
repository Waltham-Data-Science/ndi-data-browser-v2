"""FastAPI dependencies for auth.

- `get_current_session` — optional, returns None if unauthenticated.
- `require_session` — raises AuthRequired if no valid session.
"""
from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from ..clients.ndi_cloud import NdiCloudClient
from ..errors import AuthRequired
from ..observability.logging import get_logger, user_id_hash_ctx
from .session import SessionData, SessionStore, fingerprint
from .token_refresh import ensure_fresh_access_token

SESSION_COOKIE = "session"

log = get_logger(__name__)


def _get_session_store(request: Request) -> SessionStore:
    # getattr on Starlette's State returns Any; narrow to the concrete type.
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise RuntimeError("SessionStore not initialized on app.state")
    return cast(SessionStore, store)


def _get_cloud_client(request: Request) -> NdiCloudClient:
    client = getattr(request.app.state, "cloud_client", None)
    if client is None:
        raise RuntimeError("NdiCloudClient not initialized on app.state")
    return cast(NdiCloudClient, client)


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
